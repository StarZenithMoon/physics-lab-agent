"""Real bundle/filesystem/Git checks; fixture scores are not model measurements."""
import copy
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from labcore.io import read_json, write_json
from labcore.project import ROOT
from rsi import bundles, campaign
from rsi.bridge import comparable_scores, extract
from rsi.common import config, digest, fixed_digest
from rsi.reflection import compact, export
from rsi.runner import prepare_workspace, run_split
from rsi.versioning import checkpoint as archive, git


def replacement(root,name,extra='\n\n通用核验：先交付必要证据，所有结论保持适用条件。\n'):
    path=root/(name+'.md')
    path.write_text((ROOT/bundles.PATHS[name]).read_text(encoding='utf-8')+extra,encoding='utf-8')
    return path


def score(value=80):
    verdict={'task_id':'fixture-train-only','score':value,'task_passed':True,'gate_passed':True,
             'checks':[],'rubric':[],'dimension_rates':{k:value/100 for k in ('data','model','outcome','method','reproducibility','reporting')}}
    return {'split':'train','mean_score':value,'review_pending_count':0,
        'weights':{'data':15,'model':20,'outcome':25,'method':20,'reproducibility':15,'reporting':5},
        'tasks':[{'task_id':'fixture-train-only','score':value,'success_rate':1,'gate_passed':True,'task_passed':True,
                  'gate_pass_rate':1,'repetitions':[copy.deepcopy(verdict) for _ in range(3)]}]}


class BundleTests(unittest.TestCase):
    def test_all_three_components_can_be_replaced_without_changing_other_two(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);parent=bundles.create(root/'parent')
            for name in bundles.SKILLS:
                candidate=bundles.create(root/name,parent,name,replacement(root,name))
                self.assertEqual(bundles.transition(parent,candidate,name)['changed'],[name])
                self.assertEqual(digest(candidate),bundles.digest(candidate))
                bundles.structural_audit(parent,candidate,name)
                with tempfile.TemporaryDirectory() as work:
                    prepare_workspace(Path(work),{'id':'fixture','public':{},'expected':{'evidence':[]}},candidate)
                    for component,path in bundles.load(candidate).items():
                        self.assertEqual(path.read_bytes(),(Path(work)/bundles.PATHS[component]).read_bytes())

    def test_tampering_extra_files_and_two_component_changes_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);parent=bundles.create(root/'parent')
            candidate=bundles.create(root/'candidate',parent,bundles.SKILLS[0],replacement(root,bundles.SKILLS[0]))
            extra=candidate/'references.json';extra.write_text('{}')
            with self.assertRaisesRegex(ValueError,'other files'):bundles.load(candidate)
            extra.unlink()
            path=candidate/bundles.SKILLS[1]/'SKILL.md';path.write_text('tampered',encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'changed'):bundles.load(candidate)
            first=bundles.create(root/'first',parent,bundles.SKILLS[0],replacement(root,bundles.SKILLS[0]))
            second=bundles.create(root/'second',first,bundles.SKILLS[1],replacement(root,bundles.SKILLS[1]))
            with self.assertRaisesRegex(ValueError,'exactly'):bundles.transition(parent,second,bundles.SKILLS[0])

    def test_resources_and_structure_cannot_disappear(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);parent=bundles.create(root/'parent');name=bundles.SKILLS[0]
            text=(ROOT/bundles.PATHS[name]).read_text(encoding='utf-8')
            for token in ('## 可选支持资源','references/methods.md'):
                path=root/'replacement.md';path.write_text(text.replace(token,'REMOVED'),encoding='utf-8')
                candidate=bundles.create(root/str(len(token)),parent,name,path)
                with self.assertRaises(ValueError):bundles.structural_audit(parent,candidate,name)

    def test_oversized_component_is_rejected_before_loading(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);p=root/'large.md';p.write_bytes(b'x'*(bundles.MAX_SKILL_BYTES+1))
            with self.assertRaisesRegex(ValueError,'64 KiB'):bundles.text_check(p,bundles.SKILLS[0])

    def test_fixed_fingerprint_excludes_only_declared_skill_files(self):
        paths=[ROOT/p for p in bundles.PATHS.values()]
        with patch('tools.package_source.source_files',return_value=paths),patch('rsi.common.config',return_value={}):
            original=fixed_digest(bundles.PATHS.values())
            self.assertEqual(original,fixed_digest(bundles.PATHS.values()))
            self.assertNotEqual(original,fixed_digest())

    def test_prepared_runner_records_full_bundle_provenance_without_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);bundle=bundles.create(root/'bundle')
            result=run_split('train',bundle,root/'prepared',False,'fixture/not-a-real-model')
            self.assertFalse(result['executed'])
            self.assertEqual(result['skill_hashes'],bundles.hashes(bundle))
            self.assertEqual(result['fixed_hash'],fixed_digest(bundles.PATHS.values()))


class ReflectionTests(unittest.TestCase):
    def test_packet_contains_only_training_feedback_and_existing_train_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);run=root/'fixture-campaign';v0=run/'v0'
            bundles.create(v0/'bundle');write_json(v0/'train.json',score())
            write_json(v0/'selection.json',{'secret_marker':'SELECTION_MUST_NOT_APPEAR'})
            history=root/'history';archive_path=history/run.name/'v0/train-reports.zip';archive_path.parent.mkdir(parents=True)
            with ZipFile(archive_path,'w') as archive:
                archive.writestr('rep-01/reports/fixture-train-only/analysis.py','TRAIN_TRAJECTORY_EXAMPLE')
            state={'incumbent':'v0','history_directory':str(history)}
            packet=export(run,state,bundles.SKILLS[0],root/'packet',campaign.settings())
            text=(packet/'request.md').read_text(encoding='utf-8')
            self.assertIn('TRAIN_TRAJECTORY_EXAMPLE',text)
            self.assertNotIn('SELECTION_MUST_NOT_APPEAR',text)
            self.assertIn('fixture-train-only',text)
            self.assertTrue(read_json(packet/'provenance.json')['fresh_context_required'])
            self.assertEqual(read_json(packet/'provenance.json')['request_sha256'],digest(packet/'request.md'))
            with self.assertRaises(FileExistsError):export(run,state,bundles.SKILLS[0],packet,campaign.settings())

    def test_selection_or_pending_feedback_is_rejected(self):
        for change in ({'split':'selection'},{'review_pending_count':1}):
            with self.assertRaises(ValueError):compact({**score(),**change})


class CampaignTests(unittest.TestCase):
    def fixture(self,root):
        run=root/'campaign';bundles.create(run/'v0/bundle')
        value=score();write_json(run/'v0/train.json',value)
        selection={**value,'split':'selection'};write_json(run/'v0/selection.json',selection)
        state={'kind':'three-skill-rsi-campaign-v1','round':0,'incumbent':'v0',
               'incumbent_hash':bundles.digest(run/'v0/bundle'),'freeze_sha256':'fixture-freeze',
               'history_directory':str(root/'history'),'model':'fixture/not-real','harness':{'fixture':True}}
        write_json(run/'state.json',state);write_json(run/'history.json',[])
        packet=run/'proposals/v1';packet.mkdir(parents=True);(packet/'request.md').write_text('train-only-fixture')
        write_json(packet/'provenance.json',{'parent_bundle_hash':state['incumbent_hash'],'request_sha256':digest(packet/'request.md')})
        return run,state

    def prepared(self,root):
        run,state=self.fixture(root)
        response=replacement(root,bundles.SKILLS[0]);record=root/'proposer.json'
        write_json(record,{'kind':'fresh_proposer_response_v1','model':'fixture/not-real','session_id':'fixture-session',
            'operator':'unit test fixture','context_packet_sha256':digest(run/'proposals/v1/request.md'),
            'response_sha256':digest(response),'fresh_context':True,'selection_details_received':False,'test_or_private_received':False})
        with patch('rsi.campaign.state_of',return_value=(run,state)):
            candidate=campaign.build(run,response,'fixture hypothesis from train only',record)
        review=read_json(candidate/'review.json')
        return run,state,candidate,review

    def approve_fixture(self,candidate,review):
        review.update(reviewer='unit test fixture, not a real scientific review',reason='fixture',evidence='fixture')
        review['checks']={k:True for k in review['checks']};write_json(candidate/'review.json',review)

    def test_pending_or_stale_candidate_review_blocks_calls(self):
        with tempfile.TemporaryDirectory() as tmp:
            run,state,candidate,review=self.prepared(Path(tmp))
            with self.assertRaisesRegex(ValueError,'review'):campaign.validated_candidate(run,state)
            self.approve_fixture(candidate,review)
            self.assertEqual(campaign.validated_candidate(run,state)[0],'v1')
            review['candidate_hash']='stale';write_json(candidate/'review.json',review)
            with self.assertRaisesRegex(ValueError,'stale'):campaign.validated_candidate(run,state)

    def test_proposer_provenance_rejects_selection_context_or_wrong_response(self):
        for field,value in (('selection_details_received',True),('fresh_context',False),('response_sha256','wrong')):
            with tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);run,state=self.fixture(root);response=replacement(root,bundles.SKILLS[0]);record=root/'proposer.json'
                obj={'kind':'fresh_proposer_response_v1','model':'fixture/not-real','session_id':'fixture','operator':'test fixture',
                     'context_packet_sha256':digest(run/'proposals/v1/request.md'),'response_sha256':digest(response),
                     'fresh_context':True,'selection_details_received':False,'test_or_private_received':False}
                write_json(record,{**obj,field:value})
                with patch('rsi.campaign.state_of',return_value=(run,state)),self.assertRaisesRegex(ValueError,'provenance'):
                    campaign.build(run,response,'fixture hypothesis',record)
                self.assertFalse((run/'candidates/v1').exists())

    def test_plan_does_not_execute_and_partial_split_is_not_repeated(self):
        with tempfile.TemporaryDirectory() as tmp:
            run,state,candidate,review=self.prepared(Path(tmp));self.approve_fixture(candidate,review)
            with patch('rsi.campaign.state_of',return_value=(run,state)),patch('rsi.campaign.run_repeated') as worker:
                self.assertEqual(campaign.evaluate_candidate(run)['initial_sessions'],54)
                worker.assert_not_called()
                (run/'evaluations/v1/train').mkdir(parents=True)
                with self.assertRaisesRegex(ValueError,'partial split'):campaign.evaluate_candidate(run,True)
                worker.assert_not_called()

    def test_structural_failure_does_not_leave_half_registered_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);run,state=self.fixture(root);response=replacement(root,bundles.SKILLS[0]);record=root/'proposer.json'
            response.write_text(response.read_text(encoding='utf-8').replace('## 可选支持资源','REMOVED'),encoding='utf-8')
            write_json(record,{'kind':'fresh_proposer_response_v1','model':'fixture/not-real','session_id':'fixture','operator':'test fixture',
                'context_packet_sha256':digest(run/'proposals/v1/request.md'),'response_sha256':digest(response),
                'fresh_context':True,'selection_details_received':False,'test_or_private_received':False})
            with patch('rsi.campaign.state_of',return_value=(run,state)),self.assertRaisesRegex(ValueError,'structure'):
                campaign.build(run,response,'fixture hypothesis',record)
            self.assertFalse((run/'candidates/v1').exists())

    def test_archive_failure_cannot_promote_and_can_be_retried_without_model_calls(self):
        with tempfile.TemporaryDirectory() as tmp:
            run,state,candidate,review=self.prepared(Path(tmp));self.approve_fixture(candidate,review)
            change=read_json(candidate/'change.json')
            write_json(run/'evaluations/v1/status.json',{'identity':campaign.evaluation_identity(candidate,change,state)})
            train=score(90);train['freeze_sha256']='fixture-freeze'
            selection={**train,'split':'selection'}
            with patch('rsi.campaign.state_of',side_effect=lambda r:(run,read_json(run/'state.json'))), \
                 patch('rsi.campaign.checked_repeated',side_effect=lambda split,*a,**kw:((train if split=='train' else selection),'fixture/not-real')), \
                 patch('rsi.versioning.checkpoint',side_effect=[RuntimeError('fixture Git failure'),'a'*40]) as journal:
                with self.assertRaisesRegex(RuntimeError,'Git failure'):campaign.finalize(run)
                self.assertEqual(read_json(run/'state.json')['round'],0)
                self.assertEqual(read_json(run/'state.json')['incumbent'],'v0')
                result=campaign.finalize(run)
                self.assertTrue(result['accepted'])
                self.assertEqual(read_json(run/'state.json')['incumbent'],'v1')
                self.assertEqual(len(read_json(run/'history.json')),1)
                self.assertEqual(journal.call_count,2)

    def test_rejected_proposal_still_consumes_round_but_does_not_replace_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            run,state,candidate,review=self.prepared(Path(tmp));self.approve_fixture(candidate,review)
            change=read_json(candidate/'change.json')
            write_json(run/'evaluations/v1/status.json',{'identity':campaign.evaluation_identity(candidate,change,state)})
            value=score(79);value['freeze_sha256']='fixture-freeze'
            with patch('rsi.campaign.state_of',return_value=(run,state)), \
                 patch('rsi.campaign.checked_repeated',side_effect=lambda split,*a,**kw:({**value,'split':split},'fixture/not-real')), \
                 patch('rsi.versioning.checkpoint',return_value='b'*40):
                self.assertFalse(campaign.finalize(run)['accepted'])
            self.assertEqual(read_json(run/'state.json')['incumbent'],'v0')
            self.assertEqual(read_json(run/'state.json')['round'],1)
            self.assertTrue((run/'v1/decision.json').is_file())

    def test_round_schedule_covers_all_three_and_stops_at_budget(self):
        self.assertEqual([campaign.next_round({'round':i})[1] for i in range(3)],list(bundles.SKILLS))
        with self.assertRaisesRegex(ValueError,'budget'):campaign.next_round({'round':3})


class BridgeAndArchiveTests(unittest.TestCase):
    def test_score_comparison_ignores_only_missing_report_relocation(self):
        a={'score':0,'score_status':'automatic_failure','error':"[Errno 2] No such file or directory: 'C:\\old\\report.json'"}
        b={**a,'error':"[Errno 2] No such file or directory: 'C:\\new\\report.json'"}
        self.assertEqual(comparable_scores(a),comparable_scores(b))
        self.assertNotEqual(comparable_scores(a),comparable_scores({**b,'score':1}))
        self.assertNotEqual(comparable_scores(a),comparable_scores({**b,'error':'bad calculation'}))

    def test_archive_path_escape_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);zip_path=root/'bad.zip'
            with ZipFile(zip_path,'w') as stream:stream.writestr('../escape.txt','not allowed')
            with self.assertRaisesRegex(ValueError,'unsafe'):extract(zip_path,root/'extracted')
            self.assertFalse((root/'escape.txt').exists())

    @unittest.skipUnless(shutil.which('git'),'Git required')
    def test_real_git_archive_recovers_failed_commit_and_never_overwrites_payload(self):
        import rsi.versioning as versioning
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);run=root/'fixture-campaign';bundles.create(run/'v1/bundle')
            for split in ('train','selection'):write_json(root/split/'run_manifest.json',{'fixture':True})
            real_git=versioning.git
            def fail_commit(history,*args):
                if 'commit' in args:raise RuntimeError('fixture commit failed')
                return real_git(history,*args)
            with patch('rsi.versioning.git',side_effect=fail_commit):
                with self.assertRaisesRegex(RuntimeError,'commit failed'):archive(run,'v1',root/'train',root/'selection',root/'history')
            target=root/'history/fixture-campaign/v1';before=digest(target/'train-reports.zip')
            commit=archive(run,'v1',root/'train',root/'selection',root/'history',resume=True)
            self.assertEqual(len(commit),40);self.assertEqual(digest(target/'train-reports.zip'),before)
            self.assertEqual(archive(run,'v1',root/'train',root/'selection',root/'history',resume=True),commit)
            write_json(root/'train/run_manifest.json',{'tampered':True})
            with self.assertRaisesRegex(ValueError,'changed'):archive(run,'v1',root/'train',root/'selection',root/'history',resume=True)


if __name__=='__main__':unittest.main()
