from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from evaluation.rubric import specification
from labcore.io import read_json, write_json
from labcore.project import ROOT
from rsi.common import TARGET
from rsi.outer_loop import decision
from rsi.repeated import aggregate, run_repeated
from rsi.preflight import verify
from rsi.runner import prepare_workspace, event_summary


def scored(score, passed):
    return {'split':'selection','score_role':'scientific-rubric-v1','weights':{},'review_pending_count':0,
            'mean_score':score,'task_success_rate':int(passed),'evidence_compliance_rate':1,'format_compliance_rate':1,
            'tasks':[{'task_id':'a','score':score,'task_passed':passed,'gate_passed':passed,
                      'evidence_compliance_rate':1,'format_compliance_rate':1}]}


class RepeatedProtocolTests(unittest.TestCase):
    def test_aggregation_keeps_failure_and_variability(self):
        value=aggregate([scored(100,True),scored(80,False),scored(90,True)])
        self.assertEqual(value['mean_score'],90)
        self.assertEqual(value['score_sd_across_repetitions'],10)
        self.assertAlmostEqual(value['task_success_rate'],2/3)
        self.assertFalse(value['tasks'][0]['task_passed'])
        self.assertEqual(len(value['tasks'][0]['repetitions']),3)

    def test_one_run_or_pending_review_cannot_be_aggregated(self):
        with self.assertRaises(ValueError):aggregate([scored(100,True)])
        values=[scored(100,True) for _ in range(3)];values[-1]['review_pending_count']=1
        with self.assertRaisesRegex(ValueError,'reviews'):aggregate(values)

    def test_success_rate_regression_cannot_hide_in_higher_score(self):
        before=aggregate([scored(80,True),scored(80,True),scored(80,False)])
        after=aggregate([scored(90,False),scored(90,False),scored(90,False)])
        self.assertEqual(decision(before,after)['reason'],'task_regression')

    def test_one_repetition_failure_is_within_the_declared_noise_allowance(self):
        before=aggregate([scored(80,True)]*3)
        after=aggregate([scored(90,True),scored(90,True),scored(90,False)])
        outcome=decision(before,after)
        self.assertTrue(outcome['accepted'])
        self.assertEqual(outcome['regressions'],[])
        self.assertAlmostEqual(outcome['policy']['task_pass_rate_regression_limit'],1/3)

    def test_freeze_verification_detects_provider_runtime_or_source_drift(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'freeze.json';ident={'fixed_hash':'abc','model':'p/m','v0_skill_hash':'old'}
            write_json(path,{'kind':'frozen_pre_v0_protocol','identity':ident})
            with patch('rsi.preflight.identity',return_value={**ident,'model':'other/m'}):
                with self.assertRaisesRegex(ValueError,'changed'):verify(path)
            with patch('rsi.preflight.identity',return_value={**ident,'v0_skill_hash':'candidate'}):
                verify(path,allow_candidate=True)
                with self.assertRaises(ValueError):verify(path)

    def test_retry_only_infrastructure_failure_never_wrong_answer(self):
        calls=[]
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);freeze=root/'freeze.json';write_json(freeze,{'fixture':True})
            frozen={'identity':{'fixed_hash':'fixture','model':'p/m','protocol_hash':'protocol'}}
            def fake(split,skill,out,execute,model,**kwargs):
                ids=kwargs.get('task_ids');calls.append(ids)
                events=[]
                for ident in ids or ['wrong-answer','api-failure']:
                    write_json(out/ident/'report.json',{'fixture':True})
                    write_json(out/'_runner'/ident/'request.json',{'fixture':True})
                    events.append({'task_id':ident,'status':'provider_error' if ident=='api-failure' and ids is None else 'finished'})
                manifest={'tasks':events,'fixture':True};write_json(out/'run_manifest.json',manifest);return manifest
            with patch('rsi.repeated.verify',return_value=frozen),patch('rsi.runner.run_split',side_effect=fake):
                meta=run_repeated('train',ROOT/TARGET,root/'batch',freeze,True)
            self.assertEqual(calls,[None,['api-failure']]*3)
            self.assertEqual(len(meta['repetitions']),3)
            self.assertTrue((root/'batch/rep-01/attempt-01/api-failure/report.json').exists())
            self.assertTrue((root/'batch/rep-01/attempt-02/api-failure/report.json').exists())
            self.assertEqual(read_json(root/'batch/rep-01/reports/run_manifest.json')['tasks'][1]['original_attempt'],'attempt-02')

    def test_required_file_contract_visible_without_gold_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            task={'id':'fixture','public':{'instruction':'test'},'expected':{'evidence':['analysis.json'],'secret':12345}}
            prepare_workspace(root,task,ROOT/TARGET)
            public=read_json(root/'task.json')
            self.assertEqual(public['required_evidence'],['analysis.json'])
            self.assertNotIn('secret',public)
            self.assertNotIn('12345',(root/'task.json').read_text(encoding='utf-8'))

    def test_provider_zero_reported_cost_is_not_verified_free_billing(self):
        import json
        summary=event_summary(json.dumps({'type':'step_finish','part':{'cost':0,'tokens':{'input':10,'output':3}}}))
        self.assertEqual(summary['tokens']['input'],10)
        self.assertEqual(summary['provider_reported_cost'],0)
        self.assertFalse(summary['billing_cost_verified'])


if __name__=='__main__':unittest.main()
