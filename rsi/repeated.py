"""Three fresh runs with bounded infrastructure-only retries and transparent aggregation."""
import argparse
import math
from pathlib import Path
import shutil
from statistics import mean, stdev
from labcore.io import read_json, write_json
from .common import ROOT, TARGET, digest
from .preflight import protocol, verify


def same_artifacts(left,right):
    def hashes(root):
        values={}
        for p in Path(root).rglob('*'):
            if p.is_symlink() or p.is_junction():raise ValueError('linked report artifacts are not allowed')
            if p.is_file():values[p.relative_to(root).as_posix()]=digest(p)
        return values
    return hashes(left)==hashes(right)


def aggregate(values):
    if len(values) != protocol()['repetitions']:
        raise ValueError('exactly three scored repetitions required')
    if any(v.get('review_pending_count',0) for v in values):
        raise ValueError('complete scientific reviews for all repetitions')
    ids = [t['task_id'] for t in values[0]['tasks']]
    if any([t['task_id'] for t in v['tasks']] != ids for v in values):
        raise ValueError('repetition task IDs and order must match')
    if any(v.get('score_role') != values[0].get('score_role') or v.get('weights') != values[0].get('weights') for v in values):
        raise ValueError('mixed scoring protocols')
    tasks=[]
    for i, ident in enumerate(ids):
        rows=[v['tasks'][i] for v in values]
        item={'task_id':ident,'score':mean(t['score'] for t in rows),
              'score_sd':stdev(t['score'] for t in rows),
              'task_passed':all(t['task_passed'] for t in rows), 'gate_passed':all(t['gate_passed'] for t in rows),
              'success_rate':mean(int(t['task_passed']) for t in rows),
              'gate_pass_rate':mean(int(t['gate_passed']) for t in rows),
              'repetitions':rows}
        for key in ('evidence_compliance_rate','format_compliance_rate'):
            item[key]=mean(t[key] for t in rows)
        if all('dimension_rates' in t for t in rows):
            item['dimension_rates']={k:mean(t['dimension_rates'][k] for t in rows) for k in rows[0]['dimension_rates']}
        tasks.append(item)
    result = {'split':values[0]['split'],'score_role':values[0]['score_role'],'weights':values[0]['weights'],
            'repetition_count':len(values),'review_pending_count':0,'score_status':'reviewed',
            'mean_score':mean(v['mean_score'] for v in values),
            'score_sd_across_repetitions':stdev(v['mean_score'] for v in values),
            'score_min':min(v['mean_score'] for v in values),'score_max':max(v['mean_score'] for v in values),
            'task_success_rate':mean(v['task_success_rate'] for v in values),
            'evidence_compliance_rate':mean(v['evidence_compliance_rate'] for v in values),
            'format_compliance_rate':mean(v['format_compliance_rate'] for v in values), 'tasks':tasks,
            'limitations':['Three observations describe run variability, not a strong population confidence claim.']}
    if all('by_capability' in v for v in values):
        result['by_capability']={k:{'tasks':group['tasks'],
            'task_success_rate':mean(v['by_capability'][k]['task_success_rate'] for v in values),
            'mean_score':mean(v['by_capability'][k]['mean_score'] for v in values)} for k,group in values[0]['by_capability'].items()}
    return result


def run_repeated(split, skill, output, freeze_file, execute=False):
    from .runner import run_split
    saved=verify(freeze_file,allow_candidate=True)
    output=Path(output).resolve(); skill=Path(skill).resolve()
    from .bundles import FORMAT, hashes
    if skill.is_dir() != (saved['identity'].get('candidate_kind') == FORMAT):
        raise ValueError('candidate mode must match frozen protocol')
    if output.exists():raise FileExistsError('preserve existing attempts; use a fresh repetition directory')
    output.mkdir(parents=True)
    meta={'kind':'three_repetitions','split':split,'executed':execute,'skill_hash':digest(skill),
          'fixed_hash':saved['identity']['fixed_hash'],'freeze_sha256':digest(freeze_file),
          'protocol_hash':saved['identity']['protocol_hash'],'repetitions':[]}
    if skill.is_dir():meta.update(candidate_kind=FORMAT,skill_hashes=hashes(skill))
    write_json(output/'freeze-record.json',saved)
    for r in range(protocol()['repetitions']):
        verify(freeze_file,allow_candidate=True)
        rep=output/f'rep-{r+1:02d}';rep.mkdir()
        first=run_split(split,skill,rep/'attempt-01',execute,saved['identity']['model'])
        chosen={e['task_id']:(e,rep/'attempt-01') for e in first['tasks']}
        attempts=['attempt-01']
        retry=[e['task_id'] for e in first['tasks'] if e['status'] in protocol()['retry_statuses']]
        if execute and retry:
            second=run_split(split,skill,rep/'attempt-02',True,saved['identity']['model'],task_ids=retry)
            attempts.append('attempt-02')
            for event in second['tasks']:chosen[event['task_id']]=(event,rep/'attempt-02')
        effective=rep/'reports';effective.mkdir()
        events=[]
        for ident,(event,origin) in chosen.items():
            shutil.copytree(origin/ident,effective/ident)
            shutil.copytree(origin/'_runner'/ident,effective/'_runner'/ident)
            events.append({**event,'original_attempt':origin.name})
        write_json(effective/'run_manifest.json',{**first,'tasks':events,'effective_view':True,'all_attempts':attempts,
                                                'freeze_sha256':digest(freeze_file)})
        meta['repetitions'].append({'reports':effective.relative_to(output).as_posix(),'attempts':attempts})
        write_json(output/'repeat_manifest.json',meta)
    return meta


def checked_repeated(split, reports, skill_hash, model=None, harness=None):
    from .outer_loop import checked_evaluation
    reports=Path(reports).resolve()
    meta=read_json(reports/'repeat_manifest.json')
    if meta.get('kind')!='three_repetitions' or meta.get('executed') is not True or meta.get('split')!=split:
        raise ValueError('require three actual executions from the frozen protocol')
    if meta.get('skill_hash')!=skill_hash or len(meta.get('repetitions',[]))!=protocol()['repetitions']:
        raise ValueError('candidate/repetition count mismatch')
    frozen=verify(reports/'freeze-record.json',allow_candidate=True)
    from .bundles import FORMAT, fingerprint
    if frozen['identity'].get('candidate_kind') == FORMAT:
        if meta.get('candidate_kind') != FORMAT or fingerprint(meta.get('skill_hashes',{})) != skill_hash:
            raise ValueError('full three-Skill bundle provenance is required')
    if meta.get('protocol_hash')!=digest(ROOT/'evaluation/protocol.json') or meta.get('freeze_sha256')!=digest(reports/'freeze-record.json'):
        raise ValueError('repetition freeze/protocol mismatch')
    values=[];actual_model=model or frozen['identity']['model']; actual_harness=harness or frozen['identity']['harness'];seen=set();sessions=set()
    for rep in meta['repetitions']:
        path=(reports/rep['reports']).resolve()
        if not path.is_relative_to(reports) or path in seen:
            raise ValueError('repetitions must be distinct contained report directories')
        seen.add(path)
        effective=read_json(path/'run_manifest.json')
        if meta.get('candidate_kind') == FORMAT and (effective.get('candidate_kind') != FORMAT or effective.get('skill_hashes') != meta['skill_hashes']):
            raise ValueError('repetition used a different Skill bundle')
        for event in effective['tasks']:
            attempt=event.get('original_attempt')
            if attempt not in rep['attempts'] or attempt not in {'attempt-01','attempt-02'}:
                raise ValueError('effective report has no recorded raw attempt')
            origin=path.parent/attempt;ident=event['task_id']
            original=read_json(origin/'run_manifest.json')
            for key in ('split','mode','executed','skill_hash','fixed_hash','model','model_steps','timeout_seconds','resource_limits',
                        'runtime','taskset_hash','visibility','harness','candidate_kind','skill_hashes'):
                if original.get(key)!=effective.get(key):
                    raise ValueError('raw attempt execution identity differs from the effective view')
            raw={e['task_id']:e for e in original['tasks']}[ident]
            if {k:v for k,v in event.items() if k!='original_attempt'}!=raw:
                raise ValueError('effective event differs from raw attempt')
            if attempt=='attempt-02':
                previous={e['task_id']:e for e in read_json(path.parent/'attempt-01/run_manifest.json')['tasks']}
                if previous[ident]['status'] not in protocol()['retry_statuses']:
                    raise ValueError('wrong answers cannot be retried')
            if not same_artifacts(path/ident,origin/ident) or not same_artifacts(path/'_runner'/ident,origin/'_runner'/ident):
                raise ValueError('effective artifacts differ from original task output or trace')
            session=event.get('trace_summary',{}).get('session_id')
            if session:
                if session in sessions:raise ValueError('repetitions must use fresh task sessions')
                sessions.add(session)
        value,actual_model=checked_evaluation(split,path,skill_hash,actual_model,actual_harness)
        actual_harness=value['harness']
        values.append(value)
    result=aggregate(values);result['harness']=actual_harness;result['freeze_sha256']=meta['freeze_sha256']
    all_events=[]
    for rep in meta['repetitions']:
        base=(reports/rep['reports']).parent
        for attempt in rep['attempts']:
            if attempt not in {'attempt-01','attempt-02'}:raise ValueError('unknown attempt directory')
            all_events.extend(read_json(base/attempt/'run_manifest.json')['tasks'])
    costs=[e['trace_summary']['provider_reported_cost'] for e in all_events if e.get('trace_summary',{}).get('provider_reported_cost') is not None]
    usage=[e['trace_summary']['tokens'] for e in all_events if e.get('trace_summary',{}).get('tokens') is not None]
    result['execution_statistics']={'all_attempts':len(all_events),'seconds':sum(e.get('seconds',0) for e in all_events),
        'startup_wait_seconds':sum(e.get('resources',{}).get('startup_wait_seconds',0) for e in all_events),
        'elapsed_seconds':sum(e.get('resources',{}).get('elapsed_seconds',e.get('seconds',0)) for e in all_events),
        'tool_errors':sum(e.get('trace_summary',{}).get('tool_errors',0) for e in all_events),
        'tokens':{k:sum(u.get(k,0) for u in usage) for k in ('input','output','reasoning')} if usage else None,
        'provider_reported_cost':sum(costs) if costs else None,
        'billing_cost_verified':False}
    return result,actual_model


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--split',choices=['train','selection','test'],required=True)
    p.add_argument('--skill',default=str(ROOT/TARGET));p.add_argument('--out',required=True)
    p.add_argument('--freeze',required=True);p.add_argument('--execute',action='store_true')
    a=p.parse_args();v=run_repeated(a.split,a.skill,a.out,a.freeze,a.execute)
    print(f"{len(v['repetitions'])} repetitions; {'actual runs' if a.execute else 'prepared only'}; no scores invented")
