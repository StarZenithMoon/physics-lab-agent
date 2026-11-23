"""Keep each measured generation and its reports in a local-only Git journal."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
from zipfile import ZipFile, ZIP_DEFLATED
from labcore.io import read_json, write_json
from labcore.project import ROOT, runtime_info
from tools.package_source import package


def git(root,*args):
    proc=subprocess.run(['git','-C',str(root),*args],capture_output=True,text=True,encoding='utf-8',errors='replace')
    if proc.returncode:raise RuntimeError(f"Git journal command failed ({proc.returncode}): {proc.stderr.strip()}")
    return proc.stdout.strip()


def copy_files(source,destination):
    source=Path(source).resolve()
    for path in sorted(source.rglob('*')):
        if path.is_symlink() or path.is_junction():raise ValueError('journal inputs must not contain links/junctions')
        if not path.is_file() or '.git' in path.relative_to(source).parts or '__pycache__' in path.parts:continue
        target=destination/path.relative_to(source);target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(path,target)


def zip_reports(source,destination):
    source=Path(source).resolve()
    if not source.is_dir():raise FileNotFoundError('report directory does not exist')
    with ZipFile(destination,'x',compression=ZIP_DEFLATED) as archive:
        for path in sorted(source.rglob('*')):
            if path.is_symlink() or path.is_junction():raise ValueError('report archive must not contain links/junctions')
            if path.is_file() and '.git' not in path.relative_to(source).parts and '__pycache__' not in path.parts:
                archive.write(path,path.relative_to(source).as_posix())


def file_hash(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def report_archive(source,destination):
    source=Path(source)
    if source.is_file():
        with ZipFile(source) as archive:
            if archive.testzip() is not None:raise ValueError('original report ZIP is corrupt')
        shutil.copyfile(source,destination)
    else:zip_reports(source,destination)


def verify_saved_archive(target,source,train_reports,selection_reports):
    record=read_json(target/'checkpoint.json')
    files={p.relative_to(target).as_posix() for p in target.rglob('*') if p.is_file() and p.name!='checkpoint.json'}
    if files!=set(record['files']):raise ValueError('archive is partial or has unrecorded files; diagnose without model reruns')
    for name,expected in record['files'].items():
        path=(target/name).resolve()
        if not path.is_relative_to(target.resolve()) or file_hash(path)!=expected:
            raise ValueError('saved archive hash mismatch')
    def source_hashes(root):
        return {p.relative_to(root).as_posix():file_hash(p) for p in root.rglob('*')
                if p.is_file() and p.name!='git-record.json' and '__pycache__' not in p.parts}
    if source_hashes(source)!=source_hashes(target/'generation'):
        raise ValueError('generation changed after archive creation')
    for origin,name in ((train_reports,'train-reports.zip'),(selection_reports,'selection-reports.zip')):
        origin=Path(origin)
        if origin.is_file():
            if file_hash(origin)!=file_hash(target/name):raise ValueError('report archive differs')
        else:
            with ZipFile(target/name) as archive:
                saved={i.filename:hashlib.sha256(archive.read(i)).hexdigest() for i in archive.infolist() if not i.is_dir()}
            if saved!=source_hashes(origin):raise ValueError('report artifacts changed after archive creation')


def checkpoint(run,version,train_reports,selection_reports,history=None,resume=False):
    run=Path(run).resolve();history=Path(history or ROOT.parent/'evaluation-history').resolve()
    if history==ROOT or history.is_relative_to(ROOT) or ROOT.is_relative_to(history):
        raise ValueError('Git history must be a separate directory outside the public project')
    if not re.fullmatch(r'v\d+',version) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]*',run.name):
        raise ValueError('use a portable run ID and version v0/v1/...')
    source=run/version
    if not source.is_dir():raise FileNotFoundError('generation snapshot does not exist')
    history.mkdir(parents=True,exist_ok=True)
    if not (history/'.git').is_dir():
        if any(history.iterdir()):raise ValueError('use an empty directory for a new local Git journal')
        git(history,'init','--initial-branch=codex/rsi-history')
        # Raw report bytes and hashes must survive a Git checkout unchanged.
        (history/'.gitattributes').write_text('* -text\n*.zip binary\n',encoding='utf-8')
        (history/'README.md').write_text('# Local evaluation history\n\nPrivate local journal. Do not publish or add a remote. Includes rejected candidates and raw run reports.\n',encoding='utf-8')
    if git(history,'remote'):raise ValueError('evaluation history must remain local-only; use a repository with no remotes')
    target=history/run.name/version
    if target.exists():
        if not resume:raise FileExistsError('generation already archived; do not overwrite Git evidence')
        verify_saved_archive(target,source,train_reports,selection_reports)
    else:
        target.mkdir(parents=True)
        copy_files(source,target/'generation')
        report_archive(train_reports,target/'train-reports.zip');report_archive(selection_reports,target/'selection-reports.zip')
        # Archives preserve exact bytes; nested source attributes cannot change journal payloads.
        package(target/'source.zip')
        paths=[p for p in target.rglob('*') if p.is_file()]
        write_json(target/'checkpoint.json',{'kind':'generation_archive_not_independent_score_authentication',
            'run_id':run.name,'version':version,'runtime':runtime_info(),
            'files':{p.relative_to(target).as_posix():file_hash(p) for p in paths}})
    git(history,'add','--','.gitattributes','README.md',target.relative_to(history).as_posix())
    if git(history,'diff','--cached','--name-only','--',target.relative_to(history).as_posix()):
        git(history,'-c','user.name=Physics Lab Evaluator','-c','user.email=physics-lab@localhost',
            '-c','core.hooksPath=','commit','-m',f'Archive {run.name} {version} with measured reports',
            '--only','--','.gitattributes','README.md',target.relative_to(history).as_posix())
    return git(history,'log','-1','--format=%H','--',target.relative_to(history).as_posix())


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--run',required=True);p.add_argument('--version',required=True)
    p.add_argument('--train-reports',required=True);p.add_argument('--selection-reports',required=True);p.add_argument('--history')
    a=p.parse_args();print(checkpoint(a.run,a.version,a.train_reports,a.selection_reports,a.history))
