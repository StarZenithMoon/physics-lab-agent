"""Explicitly validate inheritance of resource-v5 v0; never relabel old model runs.

The original archived evaluator validates the original raw attempts and reviews.
Current baseline workspaces/prompts and execution/scoring dependencies must match.
Only optimizer/controller changes may be bridged without new baseline model calls.
"""
import json
import os
import re
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tempfile
from zipfile import ZipFile

from labcore.io import read_json
from labcore.project import ROOT
from .bundles import PATHS, sha, project_hashes
from .common import config
from .runner import prepare_workspace, task_prompt
from evaluation.evaluate import load_tasks


def extract(archive, output):
    output = Path(output).resolve()
    with ZipFile(archive) as stream:
        if sum(i.file_size for i in stream.infolist()) > 512*1024**2:
            raise ValueError('archive exceeds the bounded local import limit')
        for info in stream.infolist():
            name = PurePosixPath(info.filename)
            if name.is_absolute() or '..' in name.parts or '\\' in info.filename or ':' in info.filename:
                raise ValueError('unsafe archive path')
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError('archive links are forbidden')
            target = output.joinpath(*name.parts)
            if not target.resolve().is_relative_to(output):
                raise ValueError('archive path escapes extraction root')
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with stream.open(info) as source, target.open('wb') as dest:
                    shutil.copyfileobj(source, dest, 1024*1024)


def tree_hashes(root):
    root = Path(root)
    values = {}
    for path in sorted(root.rglob('*')):
        if path.is_symlink() or path.is_junction():
            raise ValueError('linked workspace material')
        if path.is_file() and '__pycache__' not in path.parts:
            values[path.relative_to(root).as_posix()] = sha(path)
    return values


def comparable_scores(value):
    """Relocated missing-report errors name a different directory, never a new grade."""
    if isinstance(value,list):return [comparable_scores(v) for v in value]
    if not isinstance(value,dict):return value
    result={k:comparable_scores(v) for k,v in value.items()}
    message=result.get('error')
    if (result.get('score')==0 and result.get('score_status')=='automatic_failure'
            and isinstance(message,str) and re.fullmatch(r"\[Errno 2\] No such file or directory: ['\"].*[/\\]report\.json['\"]",message)):
        result['error']='[Errno 2] missing report.json (relocated original artifacts)'
    return result


LEGACY_CHECK = r'''
import json,hashlib,sys
from pathlib import Path
from rsi.common import ROOT,TARGET,digest
from rsi.repeated import checked_repeated
from rsi.runner import prepare_workspace
from evaluation.evaluate import load_tasks
report_root=Path(sys.argv[1]);out=Path(sys.argv[2]);bundle_hash=digest(ROOT/TARGET)
results={};workspaces={}
for split in ('train','selection'):
 value,model=checked_repeated(split,report_root/split,bundle_hash)
 results[split]=value
 for task in load_tasks(split):
  work=out.parent/'old-work'/task['id'];work.mkdir(parents=True)
  prepare_workspace(work,task,ROOT/TARGET)
  workspaces[task['id']]={p.relative_to(work).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
   for p in work.rglob('*') if p.is_file() and '__pycache__' not in p.parts}
out.write_text(json.dumps({'scores':results,'workspaces':workspaces},ensure_ascii=False),encoding='utf-8')
print('Original evaluator verified 54 raw attempts, effective artifacts and scientific reviews; no model calls.')
'''


def validate(archive, baseline_bundle):
    """Return a certificate plus untouched archived score payloads; read-only archive."""
    archive = Path(archive).resolve()
    manifest = read_json(archive/'checkpoint.json')
    if manifest.get('kind') != 'generation_archive_not_independent_score_authentication':
        raise ValueError('require an original measured generation archive')
    required = {'source.zip', 'train-reports.zip', 'selection-reports.zip',
                'generation/train.json', 'generation/selection.json', 'generation/SKILL.md'}
    if not required <= set(manifest.get('files', {})):
        raise ValueError('baseline archive lacks complete provenance')
    for name, expected in manifest['files'].items():
        relative = PurePosixPath(name)
        if relative.is_absolute() or '..' in relative.parts or ':' in name or '\\' in name:
            raise ValueError('unsafe checkpoint path')
        path = archive.joinpath(*relative.parts)
        if not path.resolve().is_relative_to(archive) or sha(path) != expected:
            raise ValueError('original generation archive hash mismatch')
    from .bundles import hashes
    if hashes(baseline_bundle) != project_hashes():
        raise ValueError('bridge baseline must equal the unchanged project Skills')
    with tempfile.TemporaryDirectory(prefix='lab-rsi-v0-bridge-') as tmp:
        temp = Path(tmp)
        extract(archive/'source.zip', temp/'source')
        legacy = temp/'source/physics-lab-agent'
        sources = read_json(legacy/'SOURCE-MANIFEST.json')['files']
        for name, expected in sources.items():
            if sha(legacy/name) != expected:
                raise ValueError('original source archive manifest mismatch')
        # Match numerical code, judgements and the task-side worker, not optimizer code.
        guarded = ['labcore/', '.agents/', 'evaluation/']
        exact = ['AGENTS.md', 'docs/CONTRACTS.md', 'docs/STRUCTURE.md', 'rsi/opencode_worker.py',
                 'rsi/process_guard.py', 'rsi/checkpoint.py', 'rsi/config.json',
                 'requirements.txt', 'requirements-verified.txt']
        for name, expected in sources.items():
            if name in exact or any(name.startswith(prefix) for prefix in guarded):
                if sha(ROOT/name) != expected:
                    raise ValueError(f'baseline execution/scoring dependency changed: {name}')
        if any(sha(legacy/path) != project_hashes()[name] for name, path in PATHS.items()):
            raise ValueError('initial three Skills differ from the measured v0')
        for split in ('train','selection'):
            extract(archive/f'{split}-reports.zip', temp/'reports'/split)
        frozen = read_json(temp/'reports/train/freeze-record.json')
        # Effective local settings are copied locally only, never exported to a packet.
        local = ROOT/'rsi/config.local.json'
        if local.is_file():shutil.copyfile(local, legacy/'rsi/config.local.json')
        for name, expected in frozen['identity']['provider_settings_hashes'].items():
            if sha(ROOT/name) != expected:
                raise ValueError('provider configuration changed since v0')
            dest = legacy/name;dest.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(ROOT/name,dest)
        result = temp/'legacy-verification.json'
        environment = dict(os.environ)
        environment.update(PYTHONIOENCODING='utf-8', PYTHONDONTWRITEBYTECODE='1')
        environment.pop('PYTHONPATH',None)
        proc = subprocess.run([sys.executable,'-c',LEGACY_CHECK,str(temp/'reports'),str(result)],
                              cwd=legacy,env=environment,capture_output=True,text=True,encoding='utf-8',timeout=120)
        if proc.returncode:
            raise ValueError('original evaluator could not verify the baseline: '+proc.stderr[-3000:])
        checked = read_json(result)
        for split in ('train','selection'):
            archived = read_json(archive/f'generation/{split}.json')
            if comparable_scores(checked['scores'][split]) != comparable_scores(archived):
                raise ValueError('archived score differs from original evaluator replay')
            batch = temp/'reports'/split
            for task in load_tasks(split):
                current = temp/'new-work'/task['id'];current.mkdir(parents=True)
                prepare_workspace(current,task,baseline_bundle)
                if tree_hashes(current) != checked['workspaces'][task['id']]:
                    raise ValueError(f'task-visible baseline workspace changed: {task["id"]}')
                for rep in (1,2,3):
                    request = read_json(batch/f'rep-{rep:02d}/reports/_runner'/task['id']/'request.json')
                    if request['prompt'] != task_prompt(task,config()) or request['public'] != task['public']:
                        raise ValueError('task instructions changed since v0')
        certificate = {'kind':'explicit_baseline_inheritance_v1','new_model_calls':0,
            'measurement_protocol':frozen['protocol']['id'],
            'original_source_sha256':sha(archive/'source.zip'),
            'original_freeze_sha256':sha(temp/'reports/train/freeze-record.json'),
            'archive_checkpoint_sha256':sha(archive/'checkpoint.json'),
            'initial_skill_hashes':project_hashes(), 'task_workspace_count_compared':18,
            'checks':['original evaluator replay','raw/effective/report-review hashes',
                      'all 18 task-visible workspaces byte-identical','all 54 prompts unchanged',
                      'runtime/model/budgets/provider unchanged','numerical/scoring/worker code unchanged'],
            'missing_report_error_paths_normalized_for_comparison_only':True,
            'limitation':'Inherited v0 measurements, not new baseline sessions; no blind-human score certification.'}
        return certificate, {split:read_json(archive/f'generation/{split}.json') for split in ('train','selection')}
