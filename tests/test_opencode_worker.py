import contextlib
import io
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch
from rsi.opencode_worker import Projection,run,sse_events,quota_exhausted
from rsi.runner import event_summary


class WorkerTests(unittest.TestCase):
    def test_credit_exhaustion_is_distinct_from_concurrency_throttling(self):
        from rsi.preflight import protocol
        self.assertIn('provider_quota_exhausted',protocol()['integrity_failure_statuses'])
        self.assertNotIn('provider_quota_exhausted',protocol()['retry_statuses'])
        self.assertTrue(quota_exhausted({
            'type': 'provider.rate-limit', 'status': 429,
            'message': '请求过于频繁，超过应用的每168小时 credits 限制'}))
        self.assertTrue(quota_exhausted({'type': 'provider.error', 'status': 429,
                                        'response': {'body': '{"code":"insufficient_quota"}'}}))
        self.assertFalse(quota_exhausted({'type': 'provider.rate-limit', 'status': 429,
                                         'message': 'Too many concurrent requests'}))
        summary = event_summary(json.dumps({'type': 'error', 'error': {
            'type': 'provider.rate-limit', 'status': 429,
            'message': '请求过于频繁，超过应用的每168小时 credits 限制'}}))
        self.assertEqual(summary['provider_errors'], [{'type': 'provider_quota_exhausted', 'status': 429}])
        self.assertNotIn('credits', json.dumps(summary))

    def test_depleted_credits_stop_before_sdk_retry_and_release_server(self):
        calls=[]
        events=[{'type': 'server.connected'},
                {'type': 'session.retry.scheduled', 'data': {'sessionID': 's', 'error': {
                    'type': 'provider.rate-limit', 'status': 429,
                    'message': '请求过于频繁，超过应用的每168小时 credits 限制'}}},
                {'type': 'session.execution.succeeded', 'data': {'sessionID': 's'}}]
        frames=''.join('data: '+json.dumps(e)+'\n\n' for e in events).encode()
        def open_request(req, timeout):
            path=req.full_url.removeprefix('http://127.0.0.1:12345')
            calls.append(path)
            if path=='/api/event':return io.BytesIO(frames)
            if path=='/api/session':return io.BytesIO(b'{"data":{"id":"s"}}')
            return io.BytesIO(b'')
        server=SimpleNamespace(stdin=io.BytesIO(),stdout=io.BytesIO(b'{"url":"http://127.0.0.1:12345"}\n'),
                               wait=Mock(return_value=0),terminate=Mock())
        output=io.StringIO()
        with patch('rsi.opencode_worker.subprocess.Popen',return_value=server), \
             patch('rsi.opencode_worker.urllib.request.build_opener',return_value=SimpleNamespace(open=open_request)), \
             contextlib.redirect_stdout(output):
            self.assertEqual(run('opencode','lab-assistant','campus/max','task'),1)
        self.assertIn('provider_quota_exhausted',output.getvalue())
        self.assertNotIn('/api/session/s/wait',calls)
        self.assertTrue(server.stdin.closed)
        server.wait.assert_called_once_with(timeout=5)

    def test_stream_disconnect_is_not_success_and_frames_are_bounded(self):
        events=sse_events(io.BytesIO(b'data: {"type":"server.connected"}\n\n'))
        self.assertEqual(next(events)['type'],'server.connected')
        with self.assertRaisesRegex(RuntimeError,'disconnected'):next(events)
        with patch('rsi.opencode_worker.MAX_RESPONSE',12):
            with self.assertRaisesRegex(RuntimeError,'limit'):
                next(sse_events(io.BytesIO(b'data: '+b'x'*20+b'\n')))

    def test_projection_reconciles_missing_final_step_without_double_counting(self):
        output=io.StringIO()
        projection=Projection('s')
        with contextlib.redirect_stdout(output):
            projection.event({'type':'session.step.ended','data':{'sessionID':'s','assistantMessageID':'a',
                             'tokens':{'input':10,'output':3},'cost':0}})
            projection.event({'type':'session.tool.failed','data':{'sessionID':'s','assistantMessageID':'a','id':'t'}})
            projection.reconcile([{'type':'assistant','id':'a','tokens':{'input':10,'output':3},'content':[
                {'type':'tool','id':'t','state':{'status':'error'}}]},
                {'type':'assistant','id':'b','tokens':{'input':12,'output':4},'content':[
                    {'type':'text','text':'已达到最大步骤数'}]}])
        summary=event_summary(output.getvalue())
        self.assertEqual(summary['tokens']['input'],22)
        self.assertEqual(summary['tool_calls'],1);self.assertEqual(summary['tool_errors'],1)
        self.assertTrue(summary['step_limit'])

    def test_private_client_selects_same_agent_model_and_rejects_interactive_asks(self):
        calls=[]
        events=[{'type':'server.connected'},
                {'type':'session.step.ended','data':{'sessionID':'s','assistantMessageID':'one'}},
                {'type':'session.step.ended','data':{'sessionID':'s','assistantMessageID':'two'}},
                {'type':'permission.asked','data':{'sessionID':'s','id':'p'}},
                {'type':'form.created','data':{'form':{'sessionID':'s','id':'f'}}},
                {'type':'session.execution.succeeded','data':{'sessionID':'s'}}]
        frames=''.join('data: '+json.dumps(e)+'\n\n' for e in events).encode()
        def open_request(req,timeout):
            path=req.full_url.removeprefix('http://127.0.0.1:12345')
            body=json.loads(req.data) if req.data else None
            calls.append((req.get_method(),path,body))
            if path=='/api/event':return io.BytesIO(frames)
            if path=='/api/session':return io.BytesIO(b'{"data":{"id":"s"}}')
            if path.endswith('/context'):return io.BytesIO(b'{"data":[]}')
            return io.BytesIO(b'')
        server=SimpleNamespace(stdin=io.BytesIO(),stdout=io.BytesIO(b'{"url":"http://127.0.0.1:12345"}\n'),
                               wait=Mock(return_value=0),terminate=Mock())
        with patch('rsi.opencode_worker.Path.is_file', return_value=False), \
             patch('rsi.opencode_worker.subprocess.Popen',return_value=server), \
             patch('rsi.opencode_worker.urllib.request.build_opener',return_value=SimpleNamespace(open=open_request)), \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run('opencode','lab-assistant','campus/max','task',task_id='case',model_steps=2),0)
        reminders=[c for c in calls if c[0]=='PUT' and c[1].endswith('/instructions/entries/lab-delivery')]
        self.assertEqual(len(reminders),1)
        self.assertIn('results/report.json', reminders[0][2]['value']['instruction'])
        self.assertEqual(sum(path.endswith('/prompt') for _,path,_ in calls),1)
        self.assertEqual(calls[0][2],{'agent':'lab-assistant','model':{'providerID':'campus','id':'max'},
                                     'location':{'directory':str(__import__('pathlib').Path.cwd())}})
        permission=next(body for _,path,body in calls if path.endswith('/reply'))
        self.assertEqual(permission['decision'],'reject')
        self.assertTrue(any(method=='DELETE' and path.endswith('/form/f') for method,path,_ in calls))
        self.assertTrue(any(path.endswith('/wait') for _,path,_ in calls))


if __name__=='__main__':unittest.main()
