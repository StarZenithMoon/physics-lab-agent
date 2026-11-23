"""Freeze a source/runtime/protocol snapshot outside the public checkout."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
from labcore.io import read_json, write_json
from labcore.project import ROOT, runtime_info
from tools.package_source import package
from .common import config, digest, fixed_digest, TARGET


def protocol():
    p = read_json(ROOT/'evaluation/protocol.json')
    if p['repetitions'] != 3 or p['infrastructure_retries'] != 1:
        raise ValueError('this protocol requires three repetitions and one infrastructure retry')
    return p


def identity(bundle_mode=False):
    from .runner import find_cli, input_digest
    cfg = config()
    if not cfg['model']:
        raise ValueError('configure the actual model before freezing')
    cli = find_cli()
    if not cli:
        raise ValueError('OpenCode CLI not found')
    proc = subprocess.run([cli, '--version'], capture_output=True, text=True, encoding='utf-8', timeout=30, check=True)
    suite = read_json(ROOT/cfg['tasks'])
    for task in suite['tasks']:
        if task['public'].get('required_evidence') != task['expected']['evidence']:
            raise ValueError('required evidence must be visible in task public contract')
        if task['expected'].get('rubric', {}).get('version') != protocol()['rubric_version']:
            raise ValueError('all formal public tasks need the frozen rubric')
    # Local config is fingerprinted, never copied, and auth/credential stores are never read.
    local = [ROOT/n for n in ('.opencode/opencode.json', '.opencode/opencode.jsonc', 'opencode.json', 'opencode.jsonc')]
    from .bundles import PATHS, project_hashes, fingerprint
    value = {'fixed_hash':fixed_digest(PATHS.values() if bundle_mode else None),
            'v0_skill_hash':fingerprint(project_hashes()) if bundle_mode else digest(ROOT/TARGET), 'model':cfg['model'],
            'model_steps':cfg['model_steps'], 'timeout_seconds':cfg['timeout_seconds'],
            'resource_limits':cfg['resources'],
            'harness':{'name':'opencode','version':proc.stdout.strip(),'transport':'private-api-v2'}, 'runtime':runtime_info(),
            'protocol_hash':digest(ROOT/'evaluation/protocol.json'), 'taskset_hash':digest(ROOT/cfg['tasks']),
            'inputs_hash':input_digest(suite['tasks']),
            'provider_settings_hashes':{p.relative_to(ROOT).as_posix():digest(p) for p in local if p.is_file()}}
    if bundle_mode:
        value['candidate_kind'] = 'three-skill-bundle-v1'
    return value


def verify(path, allow_candidate=False):
    saved = read_json(path)
    if saved.get('kind') != 'frozen_pre_v0_protocol':
        raise ValueError('not a pre-v0 freeze record')
    now = identity(bundle_mode=True) if saved['identity'].get('candidate_kind') == 'three-skill-bundle-v1' else identity()
    expected = dict(saved['identity'])
    if allow_candidate:
        now.pop('v0_skill_hash'); expected.pop('v0_skill_hash')
    if now != expected:
        raise ValueError('frozen source/config/model/runtime changed; start a new protocol instead of mixing runs')
    return saved


def freeze(output, evidence, bundle_mode=False):
    output = Path(output).resolve()
    if output == ROOT or output.is_relative_to(ROOT) or ROOT.is_relative_to(output):
        raise ValueError('freeze archive must be outside the public project')
    if output.exists():
        raise FileExistsError('use a new freeze archive directory')
    evidence = Path(evidence).resolve()
    prep = read_json(evidence)
    if prep.get('ready_for_v0') is not True or prep.get('formal_baseline_executed') is not False:
        raise ValueError('require a completed preparation report, not model baseline scores')
    status = subprocess.check_output(['git','status','--porcelain'], cwd=ROOT, text=True, encoding='utf-8')
    if status.strip():
        raise ValueError('commit reviewed source before creating the freeze')
    ident = identity(bundle_mode=True) if bundle_mode else identity()
    output.mkdir(parents=True)
    package(output/'source.zip')
    (output/'preparation.json').write_bytes(evidence.read_bytes())
    record = {'kind':'frozen_pre_v0_protocol','created_at_utc':datetime.now(timezone.utc).isoformat(),'formal_baseline_executed':False,
              'git_commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
              'identity':ident, 'source_archive_sha256':digest(output/'source.zip'),
              'preparation_sha256':digest(output/'preparation.json'), 'protocol':protocol()}
    write_json(output/'freeze.json', record)
    return record


if __name__ == '__main__':
    p=argparse.ArgumentParser(); p.add_argument('--freeze'); p.add_argument('--evidence'); p.add_argument('--verify'); p.add_argument('--bundle',action='store_true')
    a=p.parse_args()
    if a.verify:
        verify(a.verify);print('Frozen v0 source/model/runtime/protocol match; no model calls')
    elif a.freeze and a.evidence:
        freeze(a.freeze,a.evidence,a.bundle);print('Protocol snapshot frozen outside project; no model calls')
    else:p.error('use --verify FILE or --freeze NEW-DIRECTORY --evidence PREPARATION.json')
