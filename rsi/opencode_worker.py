"""Small standard-library client for one private OpenCode V2 CLI server.

OpenCode still owns the Agent, Skills, permissions, tools and provider calls.
Avoid loading a second Bun CLI merely to forward a prompt to that server.
The caller MUST place this worker and all descendants in the task Job Object.
API contract: https://opencode.ai/v2/docs/api (validated against CLI 2.2.5).
"""
import argparse
import base64
import json
import os
from pathlib import Path
import queue
import secrets
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

MAX_RESPONSE=16*1024**2


def quota_exhausted(error):
    """Separate depleted provider credits from temporary request throttling."""
    if not isinstance(error, dict):
        return False
    if error.get("type") == "provider_quota_exhausted":
        return True
    description = json.dumps(error, ensure_ascii=False).lower()
    return any(marker in description for marker in (
        "insufficient_quota", "quota_exceeded", "exceeded your current quota",
        "insufficient credits", "credit balance", "每168小时 credits",
        "每 168 小时 credits", "配额已耗尽", "额度已用尽", "余额不足",
    ))


def emit(kind, session, **fields):
    print(json.dumps({'type':kind,'sessionID':session,**fields},ensure_ascii=False),flush=True)


def sse_events(stream):
    """Bounded SSE frames; a disconnect is an error, never successful completion."""
    pieces=[];size=0
    while True:
        line=stream.readline(MAX_RESPONSE+1)
        if not line:raise RuntimeError('OpenCode event stream disconnected')
        size+=len(line)
        if size>MAX_RESPONSE:raise RuntimeError('OpenCode event exceeds response limit')
        line=line.decode('utf-8').rstrip('\r\n')
        if not line:
            if pieces:
                yield json.loads('\n'.join(pieces))
            pieces=[];size=0
        elif line.startswith('data:'):
            pieces.append(line[5:].lstrip(' '))


class Projection:
    """CLI-compatible summary events plus native payloads for operator review."""
    def __init__(self,session):
        self.session=session;self.steps=set();self.tools=set();self.texts=set()

    def event(self,event):
        data=event.get('data',{});kind=event.get('type')
        if data.get('sessionID')!=self.session:return None
        message=data.get('assistantMessageID')
        fields={'native':event}
        projected='native_event'
        if kind=='session.text.ended':
            projected='text';fields['part']={'text':data['text']}
            self.texts.add((message,data.get('ordinal',0)))
        elif kind=='session.step.ended':
            projected='step_finish';fields['part']={k:data[k] for k in ('tokens','cost') if k in data}
            self.steps.add(message)
        elif kind in {'session.tool.success','session.tool.failed'}:
            projected='tool_use';fields['part']={'state':{'status':'error' if kind.endswith('failed') else 'completed'}}
            self.tools.add((message,data['id']))
        elif kind in {'session.step.failed','session.execution.failed'}:
            projected='error';fields['error']=data.get('error',{'type':'unknown'})
        emit(projected,self.session,**fields)
        return kind

    def reconcile(self,messages):
        emit('native_snapshot',self.session,messages=messages)
        for message in messages:
            if message.get('type')!='assistant':continue
            ident=message['id']
            if ident not in self.steps and message.get('tokens'):
                emit('step_finish',self.session,part={'tokens':message['tokens'],'cost':message.get('cost',0)})
                self.steps.add(ident)
            ordinal=0
            for part in message.get('content',[]):
                if part['type']=='text':
                    if (ident,ordinal) not in self.texts:emit('text',self.session,part={'text':part['text']})
                    ordinal+=1
                elif part['type']=='tool' and (ident,part['id']) not in self.tools:
                    if part['state']['status'] in {'completed','error'}:
                        emit('tool_use',self.session,part=part)


def run(exe, agent, model, prompt, task_id=None, model_steps=60, timeout_seconds=300):
    provider,model_id=model.split('/',1)
    if not provider or not model_id or '#' in model_id:raise ValueError('require plain provider/model ID')
    environment=os.environ.copy()
    password=secrets.token_urlsafe(32)
    # --stdio removes this local server credential before tool processes inherit env.
    environment['OPENCODE_PASSWORD']=password
    server=subprocess.Popen([exe,'serve','--stdio','--hostname','127.0.0.1','--port','0'],
        stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=sys.stderr,env=environment,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    startup=queue.Queue(maxsize=1)
    def listen():
        try:
            first=server.stdout.readline(MAX_RESPONSE+1)
            startup.put(json.loads(first))
            # Drain remaining server stdout without exposing local credentials.
            while server.stdout.readline(MAX_RESPONSE+1):pass
        except Exception as exc:
            if startup.empty():startup.put(exc)
    reader=threading.Thread(target=listen,daemon=True);reader.start()
    try:
        ready=startup.get(timeout=30)
        if isinstance(ready,Exception):raise RuntimeError('private server did not announce a valid endpoint')
        url=ready['url'];address=urllib.parse.urlsplit(url)
        if address.scheme!='http' or address.hostname!='127.0.0.1' or not address.port or address.username:
            raise RuntimeError('private server endpoint must be local loopback')
        auth='Basic '+base64.b64encode(('opencode:'+password).encode()).decode()
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        def request(path,body=None,method=None,stream=False,timeout=310):
            raw=json.dumps(body,ensure_ascii=False).encode() if body is not None else None
            req=urllib.request.Request(url+path,data=raw,method=method,
                headers={'Authorization':auth,'Content-Type':'application/json'})
            response=opener.open(req,timeout=timeout)
            if stream:return response
            with response:
                data=response.read(MAX_RESPONSE+1)
                if len(data)>MAX_RESPONSE:raise RuntimeError('API response too large')
                return json.loads(data) if data else None
        session=request('/api/session',{'agent':agent,'model':{'providerID':provider,'id':model_id},
                                        'location':{'directory':str(Path.cwd())}})['data']['id']
        projection=Projection(session)
        with request('/api/event',stream=True) as response:
            events=sse_events(response)
            if next(events).get('type')!='server.connected':raise RuntimeError('event subscription not ready')
            request(f'/api/session/{session}/prompt',{'text':prompt,'delivery':'steer'})
            terminal=None
            started = time.monotonic()
            reminded = False
            for event in events:
                kind=projection.event(event)
                if kind in {'session.retry.scheduled', 'session.step.failed', 'session.execution.failed'}:
                    error = event.get('data', {}).get('error', {})
                    if quota_exhausted(error):
                        emit('error', session, error={'type': 'provider_quota_exhausted',
                                                     'status': error.get('status')})
                        return 1
                if kind=='permission.asked':
                    ident=urllib.parse.quote(event['data']['id'],safe='')
                    request(f'/api/session/{session}/permission/{ident}/reply',
                            {'decision':'reject','message':'No interactive approval is available. Continue within allowed permissions.'})
                elif event.get('type')=='form.created':
                    form=event.get('data',{}).get('form',{})
                    if form.get('sessionID')==session:
                        emit('native_event',session,native=event)
                        ident=urllib.parse.quote(form['id'],safe='')
                        request(f'/api/session/{session}/form/{ident}',
                                {'message':'No interactive answer is available. Record missing facts without inventing them.'},method='DELETE')
                if kind == 'session.step.ended' and task_id and not reminded and (
                    len(projection.steps) >= max(1, model_steps // 2)
                    or time.monotonic() - started >= timeout_seconds * 0.4
                ):
                    reminded = True
                    results = Path.cwd() / 'results'
                    if not any(p.is_file() for p in (results/'report.json', results/task_id/'report.json')):
                        # The instruction update does not enqueue another user
                        # turn or reset the Agent's step allowance.
                        reminder = ("本题进入交付阶段。请停止可选重算与调试，"
                                    "核对实际已有必需文件和结果单位，并立即保存 results/report.json。"
                                    "若尚缺必需计算，只补最小必要证据；确实无法完成时说明原因。")
                        try:
                            request(f'/api/experimental/session/{session}/instructions/entries/lab-delivery',
                                    {'value': {'instruction': reminder}}, method='PUT', timeout=4)
                        except (OSError, ValueError) as exc:
                            emit('delivery_reminder_unavailable', session,
                                 status=exc.code if isinstance(exc, urllib.error.HTTPError) else None)
                        else:
                            emit('delivery_reminder', session, recorded_steps=len(projection.steps))
                if kind in {'session.execution.succeeded','session.execution.failed','session.execution.interrupted'}:
                    terminal=kind;break
            request(f'/api/experimental/session/{session}/wait',method='POST')
            messages=request(f'/api/session/{session}/context')['data']
            projection.reconcile(messages)
            if terminal!='session.execution.succeeded':
                emit('error',session,error={'type':'execution_incomplete'});return 1
        return 0
    finally:
        server.stdin.close()
        try:server.wait(timeout=5)
        except subprocess.TimeoutExpired:server.terminate();server.wait(timeout=5)
        reader.join(timeout=1)
        server.stdout.close()


def main():
    p=argparse.ArgumentParser();p.add_argument('--exe',required=True);p.add_argument('--agent',required=True)
    p.add_argument('--model',required=True);p.add_argument('--prompt',required=True)
    p.add_argument('--task-id')
    p.add_argument('--model-steps', type=int, default=60)
    p.add_argument('--timeout-seconds', type=int, default=300)
    a=p.parse_args()
    try:return run(a.exe,a.agent,a.model,a.prompt,a.task_id,a.model_steps,a.timeout_seconds)
    except Exception as exc:
        # Do not log HTTP bodies, headers, local passwords or provider credentials.
        emit('error','',error={'type':'harness_transport','exception':type(exc).__name__,
                              'status':exc.code if isinstance(exc,urllib.error.HTTPError) else None})
        return 1


if __name__=='__main__':raise SystemExit(main())
