import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from labcore.delivery import render_report, route_submission
from labcore.io import write_json, read_json
from rsi.common import ROOT, TARGET
from rsi.runner import run_split, main, prepare_workspace,workspace_manifest,workspace_changes,cleanup_workspace,event_summary


class RunnerExecutionTests(unittest.TestCase):
    def test_credit_exhaustion_saves_task_and_stops_before_next_task(self):
        with tempfile.TemporaryDirectory() as folder:
            out=Path(folder)/'run'
            scratch=[]
            def guarded(command,cwd,environment,log_directory,timeout_seconds,limits):
                scratch.append(cwd)
                (log_directory/'events.jsonl').write_text(json.dumps({'type':'error','error':{
                    'type':'provider_quota_exhausted','status':429}}),encoding='utf-8')
                return {'status':'process_failed','seconds':.01,'exit_code':1,
                        'cleanup_verified':True,'active_processes_after':0}
            with patch('rsi.runner.find_cli',return_value='fake'), \
                 patch('rsi.runner.subprocess.run',return_value=subprocess.CompletedProcess([],0,'test','')), \
                 patch('rsi.runner.run_guarded',side_effect=guarded):
                with self.assertRaisesRegex(RuntimeError,'provider_quota_exhausted'):
                    run_split('train',ROOT/TARGET,out,True,'p/m',ROOT/'evaluation/smoke-tasks.json')
            events=read_json(out/'run_manifest.json')['tasks']
            self.assertEqual(len(events),1)
            self.assertEqual(events[0]['status'],'provider_quota_exhausted')
            self.assertEqual(events[0]['workspace_cleanup']['status'],'removed')
            self.assertEqual(len(scratch),1)
            self.assertFalse(scratch[0].exists())
            self.assertTrue((out/'_runner/smoke-sum/task_event.json').is_file())

    def test_added_probe_is_distinct_from_changed_or_deleted_immutable_input(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'data').mkdir();(root/'results').mkdir()
            (root/'AGENTS.md').write_text('fixed');(root/'data/raw.csv').write_text('x\n1\n')
            before=workspace_manifest(root)
            (root/'probe.json').write_text('{"probe":1}')
            (root/'results/report.json').write_text('{}')
            change=workspace_changes(root,before)
            self.assertEqual(change['unexpected_outputs'],['probe.json'])
            self.assertEqual(change['modified'],[]);self.assertEqual(change['deleted'],[])
            (root/'data/raw.csv').write_text('x\n2\n');(root/'AGENTS.md').unlink()
            (root/'data/injected.csv').write_text('x\n3\n')
            change=workspace_changes(root,before)
            self.assertEqual(change['modified'],['data/raw.csv'])
            self.assertEqual(change['deleted'],['AGENTS.md'])
            self.assertEqual(change['added_fixed_files'],['data/injected.csv'])

    def test_output_violation_is_preserved_but_fixed_input_tamper_stops_batch(self):
        for mode in ('probe','tamper'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as folder:
                root=Path(folder);out=root/'run';taskfile=root/'tasks.json';scratch=[]
                task={'id':'a','split':'train','public':{'instruction':'test'},'expected':{'checks':[],'evidence':['intake.json']}}
                write_json(taskfile,{'visibility':'public','tasks':[task,{**task,'id':'b'}]})
                def guarded(command,cwd,environment,log_directory,timeout_seconds,limits):
                    scratch.append(cwd)
                    write_json(cwd/'results/report.json',{'status':'needs_information','result':None,'evidence':['intake.json']})
                    write_json(cwd/'results/intake.json',{'missing':['unit']})
                    (cwd/'results/report.md').write_text('report',encoding='utf-8')
                    if len(scratch)==1:
                        if mode=='probe':write_json(cwd/'probe_profile.json',{'rows':6})
                        else:(cwd/'AGENTS.md').write_text('tampered',encoding='utf-8')
                    return {'status':'finished','seconds':0,'cleanup_verified':True,'active_processes_after':0,'limits':limits}
                try:
                    with patch('rsi.runner.find_cli',return_value='fake'), \
                         patch('rsi.runner.subprocess.run',return_value=subprocess.CompletedProcess([],0,'test','')), \
                         patch('rsi.runner.run_guarded',side_effect=guarded):
                        if mode=='tamper':
                            with self.assertRaisesRegex(RuntimeError,'fixed_files_changed'):
                                run_split('train',ROOT/TARGET,out,True,'p/m',taskfile)
                        else:run_split('train',ROOT/TARGET,out,True,'p/m',taskfile)
                    first=read_json(out/'run_manifest.json')['tasks'][0]
                    if mode=='probe':
                        self.assertEqual(first['status'],'boundary_violation')
                        self.assertEqual(read_json(out/'_runner/a/unexpected-outputs/probe_profile.json'),{'rows':6})
                        self.assertEqual(len(scratch),2)
                        self.assertTrue(all(not p.exists() for p in scratch))
                    else:
                        self.assertEqual(first['workspace_changes']['modified'],['AGENTS.md'])
                        self.assertEqual(first['workspace_cleanup']['status'],'retained')
                        self.assertEqual(len(scratch),1);self.assertFalse((out/'b').exists())
                        self.assertTrue(scratch[0].exists())
                finally:
                    for path in scratch:
                        if path.exists():self.assertEqual(cleanup_workspace(path)['status'],'removed')

    def test_chinese_maximum_step_count_message_is_detected(self):
        self.assertTrue(event_summary(json.dumps({'type':'text','part':{'text':'已达当前 Agent 的最大步数上限，工具已禁用'}}))['step_limit'])

    def execute_fixture(self, mode):
        def command(args, **kw):
            if '--version' in args:
                return subprocess.CompletedProcess(args,0,'opencode test','')
            work=Path(kw['cwd'])
            if mode=='timeout':
                raise subprocess.TimeoutExpired(args,1,output=b'{"type":"text","part":{"text":"partial"}}\n',stderr=b'partial stderr')
            if mode not in {'missing','auth','steps'}:
                task=read_json(work/'task.json')
                required=task['required_evidence']
                for name in required:
                    if name.endswith('.csv'):
                        (work/'results').mkdir(exist_ok=True)
                        (work/'results'/name).write_text('x\n1\n',encoding='utf-8')
                    else:write_json(work/'results'/name,{'fixture':True})
                report={'status':'needs_information','result':None,'evidence':required}
                if mode.startswith('submission'):
                    report.update(task_id=task['id'], issues=['unit_missing'], sections={
                        'data':'题给输入', 'model':'加性模型或澄清范围', 'method':'已核对证据',
                        'results':'结果和局限', 'reproduction':'实际执行记录'})
                    if task['id']=='smoke-sum':
                        self.assertEqual(task['required_result_numbers'],['result.value','result.u'])
                        report.update(status='complete',issues=[],result={'value':5,'u':0.22360679775,'unit':'1'})
                        if mode=='submission_bad_fields':report['result']['value']={'value':5}
                else:
                    (work/'results/report.md').write_text('A task report',encoding='utf-8')
                write_json(work/'results/report.json',report)
            if mode == 'submission_timeout':
                raise subprocess.TimeoutExpired(args, 1, output=b'', stderr=b'')
            events=[]
            if mode.startswith(('steps','submission')):events=[{'type':'text','part':{'text':'已达到本 Agent 的最大步骤数'}}]
            if mode=='auth':events=[{'type':'error','error':{'type':'provider.auth','status':401}}]
            return subprocess.CompletedProcess(args,0,'\n'.join(json.dumps(v,ensure_ascii=False) for v in events),'')
        def guarded(args,cwd,environment,log_directory,timeout_seconds,limits):
            try:
                proc=command(args,cwd=cwd)
                stdout,stderr,status=proc.stdout,proc.stderr,'finished'
            except subprocess.TimeoutExpired as exc:
                stdout,stderr,status=exc.stdout.decode(),exc.stderr.decode(),'timeout'
            (log_directory/'events.jsonl').write_text(stdout,encoding='utf-8')
            (log_directory/'stderr.log').write_text(stderr,encoding='utf-8')
            return {'status':status,'seconds':.01,'exit_code':0,'cleanup_verified':True,'active_processes_after':0}
        with tempfile.TemporaryDirectory() as folder:
            out=Path(folder)/'run'
            with patch('rsi.runner.find_cli',return_value='fake-opencode'),patch('rsi.runner.subprocess.run',side_effect=command),patch('rsi.runner.run_guarded',side_effect=guarded):
                manifest=run_split('train',ROOT/TARGET,out,execute=True,model='example/model',task_path=ROOT/'evaluation/smoke-tasks.json')
            events=[(out/'_runner'/t['task_id']/'events.jsonl').read_text(encoding='utf-8') for t in manifest['tasks']]
            if mode.startswith('submission'):
                sum_output=out/'smoke-sum'
                clarify_output=out/'smoke-missing'
                self.assertIn('## 结果与局限',(clarify_output/'report.md').read_text(encoding='utf-8'))
                self.assertIsNone(read_json(clarify_output/'report.json')['result'])
                if mode=='submission_bad_fields':
                    self.assertFalse((sum_output/'report.md').exists())
                else:
                    self.assertIn('0.22360679775',(sum_output/'report.md').read_text(encoding='utf-8'))
                    self.assertEqual(read_json(sum_output/'report.json')['result']['u'],0.22360679775)
            return manifest,events

    def test_exit_zero_without_reports_is_incomplete(self):
        manifest,_=self.execute_fixture('missing')
        self.assertEqual({t['status'] for t in manifest['tasks']},{'incomplete_output'})
        self.assertTrue(all('missing/invalid report.md' in t['output_problems'] for t in manifest['tasks']))

    def test_cli_errors_and_step_limit_do_not_become_success(self):
        for mode,status in [('auth','provider_error'),('steps','step_limit')]:
            with self.subTest(mode=mode):
                manifest,_=self.execute_fixture(mode)
                self.assertEqual({t['status'] for t in manifest['tasks']},{status})

    def test_timeout_keeps_partial_trace(self):
        manifest,events=self.execute_fixture('timeout')
        self.assertEqual({t['status'] for t in manifest['tasks']},{'timeout'})
        self.assertTrue(all('partial' in e for e in events))

    def test_completed_artifacts_at_last_step_are_still_delivered(self):
        manifest,_=self.execute_fixture('steps_complete')
        self.assertEqual({t['status'] for t in manifest['tasks']},{'finished'})
        self.assertTrue(all(t['completed_at_step_limit'] for t in manifest['tasks']))

    def test_explicit_submission_at_last_step_generates_both_deliverables(self):
        manifest,_=self.execute_fixture('submission')
        self.assertEqual({t['status'] for t in manifest['tasks']},{'finished'})
        self.assertTrue(all(t['report_delivery']=='markdown_rendered_from_submission' for t in manifest['tasks']))

    def test_structured_report_already_on_disk_survives_timeout(self):
        manifest, _ = self.execute_fixture('submission_timeout')
        self.assertTrue(all(t['completed_at_timeout'] for t in manifest['tasks']))
        self.assertTrue(all(t['status'] == 'finished' for t in manifest['tasks']))
        self.assertTrue(all(t['resources']['status'] == 'timeout' for t in manifest['tasks']))

    def test_invalid_numeric_shape_preserves_step_limit_and_records_failure(self):
        manifest,_=self.execute_fixture('submission_bad_fields')
        failed=next(t for t in manifest['tasks'] if t['task_id']=='smoke-sum')
        self.assertEqual(failed['status'],'step_limit')
        self.assertIn('result.value must be a finite JSON number',failed['output_problems'])

    def test_complete_output_contract_is_recorded(self):
        manifest,_=self.execute_fixture('normal')
        self.assertEqual({t['status'] for t in manifest['tasks']},{'finished'})
        self.assertTrue(all(not t['output_problems'] for t in manifest['tasks']))

    def test_cli_returns_failure_when_task_is_incomplete(self):
        with patch('rsi.runner.run_split',return_value={'mode':'opencode','tasks':[{'task_id':'sample','status':'incomplete_output'}]}):
            self.assertEqual(main(['--split','train','--out','unused','--execute']),1)

    def test_limited_result_can_be_null_if_task_requires_no_numbers(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            report_path = root / "report.json"
            report = {
                "task_id": "divergent", "status": "limited", "result": None,
                "issues": ["undefined_moments"], "evidence": ["analysis.json"],
                "sections": dict.fromkeys(("data", "model", "method", "results", "reproduction"),
                                          "Explanation and evidence"),
            }
            task = {"id": "divergent", "expected": {"checks": [], "evidence": ["analysis.json"]}}
            write_json(report_path, report)
            write_json(root / "analysis.json", {"verification": "fixture"})
            self.assertTrue(render_report(report_path, task=task))
            self.assertIn("Explanation and evidence", (root / "report.md").read_text(encoding="utf-8"))
            task["expected"]["checks"] = [{"path": "result.u", "target": 0.1}]
            with self.assertRaisesRegex(ValueError, "result.u must be a finite JSON number"):
                render_report(report_path, task=task)
            self.assertIn("Explanation and evidence", (root / "report.md").read_text(encoding="utf-8"))


    def test_route_nested_submission_and_keep_science_and_raw_report(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            results = root / "results"
            nested = results / "sample"
            write_json(nested / "analysis.json", {"value": 5})
            (nested / "display.md").write_text("Actual display", encoding="utf-8")
            original = {
                "task_id": "sample", "status": "limited", "result": {"value": 5},
                "issues": ["extrapolation: explanation", "Other discussion"],
                "evidence": ["analysis.json", "display.md"], "rounding_file": "display.md",
                "sections": dict.fromkeys(("data", "model", "method", "results", "reproduction"), "details"),
            }
            write_json(nested / "report.json", original)
            task = {"id": "sample", "expected": {"evidence": ["analysis.json"]}}
            recovery = route_submission(results, task, root / "original.json")
            self.assertEqual(recovery["report_from"], "sample/report.json")
            self.assertEqual(recovery["invalid_optional_rounding"], "display.md")
            self.assertEqual(read_json(root / "original.json"), original)
            recovered = read_json(results / "report.json")
            self.assertEqual(recovered["result"], original["result"])
            self.assertEqual(recovered["issues"], ["extrapolation", "Other discussion"])
            self.assertNotIn("rounding_file", recovered)
            self.assertEqual((results / "analysis.json").read_bytes(), (nested / "analysis.json").read_bytes())
            self.assertTrue(render_report(results / "report.json", task=task))

    def test_route_nested_evidence_from_flat_report_without_overwriting_conflict(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            nested = root / "sample"
            write_json(nested / "analysis.json", {"value": 5})
            report = {"task_id": "sample", "sections": {}, "evidence": ["sample/analysis.json"]}
            write_json(root / "report.json", report)
            task = {"id": "sample", "expected": {"evidence": ["analysis.json"]}}
            write_json(root / "analysis.json", {"value": 7})
            with self.assertRaisesRegex(ValueError, "ambiguous evidence"):
                route_submission(root, task, root / "original.json")
            self.assertEqual(read_json(root / "analysis.json"), {"value": 7})
            self.assertEqual(read_json(root / "report.json"), report)
            (root / "analysis.json").unlink()
            recovery = route_submission(root, task, root / "original.json")
            self.assertEqual(recovery["evidence_names"], {"sample/analysis.json": "analysis.json"})
            self.assertEqual(read_json(root / "analysis.json"), {"value": 5})


    def test_route_results_prefixed_rounding_and_evidence(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            results = root / "results"
            write_json(results / "fit.json", {"value": 5})
            write_json(results / "rounding.json", {"value": {"value": "5.0", "u": "1.0"}})
            original = {
                "task_id": "sample", "status": "complete", "result": {"value": 5},
                "evidence": ["results/fit.json", "rounding.json"], "rounding_file": "results/rounding.json",
                "sections": dict.fromkeys(("data", "model", "method", "results", "reproduction"), "details"),
            }
            write_json(results / "report.json", original)
            task = {"id": "sample", "expected": {"evidence": ["fit.json"]}}
            recovery = route_submission(results, task, root / "original.json")
            self.assertEqual(recovery["rounding_route"], "rounding.json")
            self.assertEqual(read_json(root / "original.json"), original)
            self.assertTrue(render_report(results / "report.json", task=task))
            self.assertIn("(5.0 ± 1.0)", (results / "report.md").read_text(encoding="utf-8"))

    def test_old_case_delivery_suffix_is_removed_without_touching_science(self):
        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder)
            task = {
                "id": "sample",
                "public": {"instruction": "给定模型与单位。 输出直接放 results/，包含 report.json/report.md；数值结果保留保护位数，实际执行并引用证据。"},
                "expected": {"evidence": ["analysis.json"], "checks": [{"path": "result.u", "target": 12.3}]},
            }
            prepare_workspace(work, task, ROOT / TARGET)
            public = read_json(work / "task.json")
            self.assertEqual(public["instruction"], "给定模型与单位。 数值结果保留保护位数，实际执行并引用证据。")
            self.assertEqual(public["required_result_numbers"], ["result.u"])
            self.assertEqual(public["required_evidence"], ["analysis.json"])
            self.assertNotIn("expected", public)
            self.assertIn("report.json/report.md", task["public"]["instruction"])

    def test_workspace_does_not_include_smoke_answers_or_developer_guides(self):
        with tempfile.TemporaryDirectory() as folder:
            work=Path(folder)
            prepare_workspace(work,{'id':'test','public':{'instruction':'task'}},ROOT/TARGET)
            self.assertTrue((work/'docs/CONTRACTS.md').is_file())
            self.assertFalse((work/'docs/SMOKE.md').exists())
            self.assertFalse((work/'docs/EVALUATION-DESIGN.md').exists())
            self.assertFalse((work/'evaluation').exists())

    def test_checkpoint_survives_cleanup_failure_and_next_task_never_starts(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);output=root/'run';task_file=root/'tasks.json'
            task={'id':'a','split':'train','public':{'instruction':'test'},'expected':{'checks':[],'evidence':['intake.json']}}
            write_json(task_file,{'visibility':'public','tasks':[task,{**task,'id':'b'}]})
            def guarded(command,cwd,environment,log_directory,timeout_seconds,limits):
                write_json(cwd/'results/report.json',{'status':'needs_information','result':None,'evidence':['intake.json']})
                write_json(cwd/'results/intake.json',{'missing':['unit']})
                (cwd/'results/report.md').write_text('saved first',encoding='utf-8')
                (log_directory/'events.jsonl').write_text('',encoding='utf-8')
                return {'status':'finished','seconds':0,'cleanup_verified':True,'active_processes_after':0}
            def cleanup(work):
                # The real output and manifest exist while the scratch directory still exists.
                self.assertTrue(work.exists())
                self.assertTrue((output/'a/report.md').is_file())
                self.assertEqual(read_json(output/'run_manifest.json')['tasks'][0]['status'],'finished')
                import shutil
                shutil.rmtree(work)
                return {'status':'retained','error':'simulated external file lock'}
            with (patch('rsi.runner.find_cli',return_value='fake'),
                  patch('rsi.runner.subprocess.run',return_value=subprocess.CompletedProcess([],0,'test','')),
                  patch('rsi.runner.run_guarded',side_effect=guarded),patch('rsi.runner.cleanup_workspace',side_effect=cleanup)):
                with self.assertRaisesRegex(RuntimeError,'cleanup_failed'):
                    run_split('train',ROOT/TARGET,output,True,'p/m',task_file)
            self.assertEqual(read_json(output/'run_manifest.json')['tasks'][0]['status'],'cleanup_failed')
            self.assertFalse((output/'b').exists())
