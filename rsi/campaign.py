"""Coordinate-wise three-Skill RSI; explicit review, measured selection, archive first.

No command generates a candidate or calls a model implicitly. `evaluate --execute`
is the only model-execution entry; whole completed splits can be resumed. Partial
or ambiguous splits stop for diagnosis rather than silently repeating paid tasks.
"""
import argparse
import csv
import difflib
import io
import json
import os
from pathlib import Path
import shutil
import tempfile

from labcore.io import read_json, write_json
from labcore.project import ROOT
from . import bundles
from .checkpoint import checkpoint
from .common import config, digest, fixed_digest
from .outer_loop import decision
from .preflight import verify
from .repeated import run_repeated, checked_repeated


def settings():
    value=read_json(ROOT/'rsi/optimization.json')
    if value.get('kind')!='coordinate-skill-optimization-v1' or value.get('repetitions')!=3:
        raise ValueError('unsupported optimization policy')
    if value.get('max_candidates')!=len(value.get('schedule',[])) or set(value['schedule'])!=set(bundles.SKILLS):
        raise ValueError('declare a three-component candidate schedule before freezing')
    if (value.get('selection_min_gain')!=0.0
            or value.get('max_per_task_regression')!=3.0
            or value.get('max_per_task_pass_rate_regression')!=1/3
            or value.get('overall_observation_regression_allowance')!=1):
        raise ValueError('this campaign requires the declared noise-aware selection rule')
    return value


def state_of(run):
    run=Path(run).resolve();state=read_json(run/'state.json')
    if state.get('kind')!='three-skill-rsi-campaign-v1':raise ValueError('not a bundle campaign')
    frozen=verify(run/'freeze-record.json',allow_candidate=True)
    if frozen['identity'].get('candidate_kind')!=bundles.FORMAT:
        raise ValueError('require a bundle freeze')
    if state['freeze_sha256']!=digest(run/'freeze-record.json') or state['fixed_hash']!=fixed_digest(bundles.PATHS.values()):
        raise ValueError('experiment identity changed')
    if bundles.digest(run/state['incumbent']/'bundle')!=state['incumbent_hash']:
        raise ValueError('incumbent snapshot changed')
    settings()
    return run,state


def initialize(run, freeze, archive=None, history=None, train_reports=None, selection_reports=None):
    from .bridge import validate
    from .versioning import checkpoint as archive_version
    run=Path(run).resolve();archive=Path(archive).resolve() if archive else None
    if run.exists():raise FileExistsError('use a fresh campaign directory; preserve legacy v0')
    frozen=verify(freeze)
    if frozen['identity'].get('candidate_kind')!=bundles.FORMAT:raise ValueError('use preflight --bundle')
    settings()
    with tempfile.TemporaryDirectory(prefix='lab-rsi-baseline-') as tmp:
        initial=bundles.create(Path(tmp)/'bundle')
        if archive:
            if train_reports or selection_reports:raise ValueError('choose inherited or fresh baseline, not both')
            certificate,scores=validate(archive,initial)
            train_input,selection_input=archive/'train-reports.zip',archive/'selection-reports.zip'
            origin='explicitly inherited measured resource-v5 v0; not re-executed'
        else:
            if not train_reports or not selection_reports:raise ValueError('provide both actual repeated baseline report directories')
            scores={}
            for split,reports in (('train',train_reports),('selection',selection_reports)):
                value,_=checked_repeated(split,reports,bundles.digest(initial),frozen['identity']['model'],frozen['identity']['harness'])
                if value['freeze_sha256']!=digest(freeze):raise ValueError('fresh baseline freeze differs')
                scores[split]=value
            certificate={'kind':'fresh_measured_three_skill_baseline','freeze_sha256':digest(freeze)}
            train_input,selection_input=train_reports,selection_reports
            origin='fresh measured three-Skill baseline'
        if frozen['identity']['v0_skill_hash']!=bundles.digest(initial):raise ValueError('frozen initial bundle differs')
        run.mkdir(parents=True);v0=run/'v0';v0.mkdir()
        shutil.copytree(initial,v0/'bundle')
        for split,value in scores.items():write_json(v0/f'{split}.json',value)
        write_json(v0/'baseline-origin.json',certificate)
        if archive:
            shutil.copyfile(archive/'source.zip',v0/'original-v0-source.zip')
            shutil.copyfile(archive/'checkpoint.json',v0/'original-v0-checkpoint.json')
        shutil.copyfile(freeze,run/'freeze-record.json')
        model=frozen['identity']['model']
        state={'kind':'three-skill-rsi-campaign-v1','round':0,'incumbent':'v0',
               'incumbent_hash':bundles.digest(initial),'model':model,'harness':frozen['identity']['harness'],
               'fixed_hash':frozen['identity']['fixed_hash'],'freeze_sha256':digest(run/'freeze-record.json'),
               'baseline_origin':origin,
               'history_directory':str(Path(history).resolve()) if history else str(ROOT.parent/'evaluation-history')}
        commit=archive_version(run,'v0',train_input,selection_input,history)
        checkpoint(v0/'git-record.json',{'commit':commit,'journal':'local-only outside public project'})
        checkpoint(run/'history.json',[])
        checkpoint(run/'state.json',state)
    return state


def next_round(state):
    policy=settings();step=state['round']+1
    if step>policy['max_candidates']:raise ValueError('candidate budget exhausted')
    version=state.get('version_offset',0)+step
    return f'v{version}',policy['schedule'][step-1]


def propose(run):
    from .reflection import export
    run,state=state_of(run);version,target=next_round(state)
    return export(run,state,target,run/'proposals'/version,settings())


def build(run,response,hypothesis,proposer_record):
    run,state=state_of(run);version,target=next_round(state)
    packet=run/'proposals'/version
    provenance=read_json(packet/'provenance.json')
    if provenance['parent_bundle_hash']!=state['incumbent_hash'] or provenance['request_sha256']!=digest(packet/'request.md'):
        raise ValueError('proposer packet changed or was prepared for a different parent')
    if not hypothesis.strip():raise ValueError('record a train-grounded change hypothesis')
    proposer=read_json(proposer_record)
    required={'kind','model','session_id','operator','context_packet_sha256','response_sha256',
              'fresh_context','selection_details_received','test_or_private_received'}
    if (set(proposer)!=required or proposer['kind']!='fresh_proposer_response_v1'
            or proposer['context_packet_sha256']!=provenance['request_sha256']
            or proposer['response_sha256']!=digest(response) or proposer['fresh_context'] is not True
            or proposer['selection_details_received'] is not False or proposer['test_or_private_received'] is not False
            or any(not isinstance(proposer[k],str) or not proposer[k].strip() for k in ('model','session_id','operator'))):
        raise ValueError('record the actual fresh, train-only proposer response provenance')
    candidate=run/'candidates'/version
    if candidate.exists():raise FileExistsError('preserve the registered candidate')
    candidate.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(dir=candidate.parent,prefix=f'.{version}-') as tmp:
        staged=Path(tmp)
        bundle=bundles.create(staged/'bundle',run/state['incumbent']/'bundle',target,response)
        audit=bundles.structural_audit(run/state['incumbent']/'bundle',bundle,target)
        change={**bundles.transition(run/state['incumbent']/'bundle',bundle,target),
                'hypothesis':hypothesis,'proposal_request_sha256':provenance['request_sha256'],
                'response_sha256':digest(response),'proposer':proposer,'mechanical_audit':audit}
        write_json(staged/'change.json',change)
        review={'candidate_hash':change['candidate_hash'],'parent_hash':change['parent_hash'],'target':target,
                'change_sha256':digest(staged/'change.json'),'reviewer':'','reason':'','evidence':'',
                'checks':{key:None for key in ('scientific_boundaries_preserved','supporting_resources_preserved',
                        'one_skill_scope','no_case_specific_answers','fresh_train_only_proposer_context')}}
        write_json(staged/'review.json',review)
        # An invalid response cannot leave a half-registered candidate blocking the round.
        os.rename(staged,candidate)
    return candidate


def validated_candidate(run,state):
    version,target=next_round(state);candidate=run/'candidates'/version
    bundle=candidate/'bundle';change=read_json(candidate/'change.json');review=read_json(candidate/'review.json')
    actual=bundles.transition(run/state['incumbent']/'bundle',bundle,target)
    bundles.structural_audit(run/state['incumbent']/'bundle',bundle,target)
    if any(change.get(k)!=v for k,v in actual.items()):raise ValueError('candidate provenance changed')
    if any(review.get(k)!=actual[k] for k in ('candidate_hash','parent_hash','target')):
        raise ValueError('semantic review is stale')
    if review.get('change_sha256')!=digest(candidate/'change.json'):
        raise ValueError('change provenance changed after review')
    required={'scientific_boundaries_preserved','supporting_resources_preserved','one_skill_scope',
              'no_case_specific_answers','fresh_train_only_proposer_context'}
    if set(review.get('checks',{}))!=required or any(v is not True for v in review['checks'].values()):
        raise ValueError('complete independent candidate review before model evaluation')
    if any(not isinstance(review.get(k),str) or not review[k].strip() for k in ('reviewer','reason','evidence')):
        raise ValueError('candidate review needs reviewer, reasons and evidence')
    return version,candidate,change,review


def evaluation_identity(candidate,change,state):
    return {'candidate_hash':change['candidate_hash'],'parent_hash':change['parent_hash'],
            'review_sha256':digest(candidate/'review.json'),'change_sha256':digest(candidate/'change.json'),
            'freeze_sha256':state['freeze_sha256']}


def evaluate_candidate(run,execute=False):
    run,state=state_of(run);version,candidate,change,review=validated_candidate(run,state)
    identity=evaluation_identity(candidate,change,state)
    record=run/'evaluations'/version/'status.json'
    if record.exists():
        old=read_json(record)
        if old['identity']!=identity:raise ValueError('evaluation registration changed')
    elif execute:
        checkpoint(record,{'identity':identity,'phase':'registered','completed_splits':[]})
    if not execute:
        return {'mode':'plan_only','version':version,'target':change['target'],'initial_sessions':54,
                'maximum_sessions_with_one_infra_retry_per_task':108,'identity':identity}
    completed=[]
    for split in ('train','selection'):
        out=record.parent/split
        if out.exists():
            manifest=read_json(out/'repeat_manifest.json') if (out/'repeat_manifest.json').exists() else {}
            if len(manifest.get('repetitions',[]))!=3 or manifest.get('skill_hash')!=identity['candidate_hash']:
                raise ValueError('partial split needs diagnosis; preserve it, do not silently rerun model tasks')
            if manifest.get('freeze_sha256')!=state['freeze_sha256']:
                raise ValueError('completed split uses a different freeze')
        else:
            checkpoint(record,{'identity':identity,'phase':f'running_{split}','completed_splits':completed})
            run_repeated(split,candidate/'bundle',out,run/'freeze-record.json',execute=True)
        completed.append(split)
        checkpoint(record,{'identity':identity,'phase':'awaiting_scientific_reviews','completed_splits':completed})
    return {'version':version,'status':'awaiting_scientific_reviews','completed_splits':completed}


def review_templates(run):
    from evaluation.evaluate import load_tasks
    from evaluation.rubric import review_template
    run,state=state_of(run);version,_,_,_=validated_candidate(run,state)
    created=0
    for split in ('train','selection'):
        batch=run/'evaluations'/version/split
        meta=read_json(batch/'repeat_manifest.json')
        for rep in meta['repetitions']:
            reports=batch/rep['reports']
            for task in load_tasks(split):
                path=reports/'_reviews'/(task['id']+'.json')
                if path.exists():continue
                try:
                    report=read_json(reports/task['id']/'report.json')
                    if not isinstance(report,dict):continue
                except (OSError,ValueError):continue
                checkpoint(path,review_template(task,reports/task['id']));created+=1
    return {'unfilled_reviews_created':created,'grades_assigned':0}


def dimensions(score):
    rows=[r for t in score['tasks'] for r in t['repetitions']]
    return {key:{'maximum':weight,'mean_points':weight*sum(r['dimension_rates'][key] for r in rows)/len(rows)}
            for key,weight in score['weights'].items()}


def finalize(run):
    from .versioning import checkpoint as archive_version
    run,state=state_of(run);version,candidate,change,review=validated_candidate(run,state)
    registration=read_json(run/'evaluations'/version/'status.json')
    if registration['identity']!=evaluation_identity(candidate,change,state):
        raise ValueError('candidate provenance/review changed after evaluation registration')
    base=run/'evaluations'/version
    train,_=checked_repeated('train',base/'train',change['candidate_hash'],state['model'],state['harness'])
    selection,_=checked_repeated('selection',base/'selection',change['candidate_hash'],state['model'],state['harness'])
    if train['freeze_sha256']!=state['freeze_sha256'] or selection['freeze_sha256']!=state['freeze_sha256']:
        raise ValueError('candidate freeze differs from this campaign')
    previous=read_json(run/state['incumbent']/'selection.json')
    policy=settings()
    outcome=decision(previous,selection,policy['selection_min_gain'],policy['max_per_task_regression'],
                     policy['max_per_task_pass_rate_regression'],
                     policy['overall_observation_regression_allowance'])
    event={'round':state.get('version_offset',0)+state['round']+1,'version':version,'target':change['target'],
           'parent_hash':change['parent_hash'],'candidate_hash':change['candidate_hash'],
           'train':train['mean_score'],'selection':selection['mean_score'],**outcome,
           'per_task_score_deltas':{a['task_id']:b['score']-a['score'] for a,b in zip(previous['tasks'],selection['tasks'])},
           'uncertainty_note':'Three repetitions are descriptive; task-matched deltas are not a statistical significance claim.'}
    payload={'train.json':train,'selection.json':selection,'decision.json':event,
             'change.json':change,'candidate-review.json':review,
             'dimensions.json':{'train':dimensions(train),'selection':dimensions(selection)}}
    folder=run/version
    if folder.exists():
        if bundles.digest(folder/'bundle')!=change['candidate_hash'] or any(read_json(folder/k)!=v for k,v in payload.items()):
            raise ValueError('existing generation differs; preserve it for diagnosis')
    else:
        folder.mkdir();shutil.copytree(candidate/'bundle',folder/'bundle')
        for name,value in payload.items():checkpoint(folder/name,value)
        diff=''
        for name,path in bundles.load(candidate/'bundle').items():
            before=bundles.load(run/state['incumbent']/'bundle')[name]
            diff+=''.join(difflib.unified_diff(before.read_text(encoding='utf-8').splitlines(True),path.read_text(encoding='utf-8').splitlines(True),fromfile=f'incumbent/{name}',tofile=f'{version}/{name}'))
        (folder/'diff.patch').write_text(diff,encoding='utf-8')
    # Archive first. A Git failure cannot silently advance the incumbent/round.
    commit=archive_version(run,version,base/'train',base/'selection',state['history_directory'],resume=True)
    checkpoint(folder/'git-record.json',{'commit':commit,'journal':'local-only outside public project'})
    history=read_json(run/'history.json')
    if not any(h['version']==version for h in history):history.append(event)
    elif next(h for h in history if h['version']==version)!=event:raise ValueError('history differs')
    checkpoint(run/'history.json',history)
    stream=io.StringIO(newline='');writer=csv.DictWriter(stream,fieldnames=['round','version','target','train','selection','accepted','gain'])
    writer.writeheader();writer.writerows({k:h[k] for k in writer.fieldnames} for h in history)
    (run/'curve.csv').write_text(stream.getvalue(),encoding='utf-8')
    if outcome['accepted']:state.update(incumbent=version,incumbent_hash=change['candidate_hash'])
    state['round']+=1
    checkpoint(run/'state.json',state)
    return event


def status(run):
    run,state=state_of(run)
    value={'rounds_decided':state['round'],'incumbent':state['incumbent'],'incumbent_hash':state['incumbent_hash'],
           'candidate_budget':settings()['max_candidates'],'saved_attempts':0,'statuses':{},'active':[],
           'baseline_origin':state['baseline_origin']}
    if state['round']<settings()['max_candidates']:
        version,target=next_round(state)
        value['next_candidate']={'version':version,'target':target,
            'proposal_exported':(run/'proposals'/version/'request.md').is_file(),
            'candidate_registered':(run/'candidates'/version/'bundle'/'bundle.json').is_file()}
    for path in sorted((run/'evaluations').glob('v*/**/attempt-*/run_manifest.json')):
        for event in read_json(path)['tasks']:
            value['saved_attempts']+=1;key=event['status']
            value['statuses'][key]=value['statuses'].get(key,0)+1
    for path in sorted((run/'evaluations').glob('v*/**/attempt-*/_runner/*/task_state.json')):
        task=read_json(path)
        if task.get('phase')!='complete':value['active'].append({'task':path.parent.name,'state':task})
    return value


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=['init','propose','build','evaluate','reviews','finalize','status'])
    p.add_argument('--run',required=True);p.add_argument('--freeze');p.add_argument('--legacy-archive');p.add_argument('--history')
    p.add_argument('--train-reports');p.add_argument('--selection-reports')
    p.add_argument('--response');p.add_argument('--proposer-record');p.add_argument('--hypothesis',default='');p.add_argument('--execute',action='store_true')
    a=p.parse_args()
    if a.action=='init':
        if not a.freeze:p.error('init requires --freeze')
        result=initialize(a.run,a.freeze,a.legacy_archive,a.history,a.train_reports,a.selection_reports)
    elif a.action=='propose':result={'packet':str(propose(a.run)),'model_calls':0}
    elif a.action=='build':
        if not a.response or not a.proposer_record:p.error('build requires --response and --proposer-record for actual fresh-proposer output')
        result={'candidate':str(build(a.run,a.response,a.hypothesis,a.proposer_record)),'review':'pending independent semantic review'}
    elif a.action=='evaluate':result=evaluate_candidate(a.run,a.execute)
    elif a.action=='reviews':result=review_templates(a.run)
    elif a.action=='finalize':result=finalize(a.run)
    else:result=status(a.run)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
