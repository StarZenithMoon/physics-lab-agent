"""Prepare/execute fresh OpenCode tasks; no hidden answers copied into workspaces.

This is filesystem separation, not an OS security boundary. --execute is explicit.
"""
import argparse
from collections import Counter
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from labcore.delivery import render_report, required_result_numbers, route_submission
from labcore.io import read_json, write_json
from labcore.project import runtime_info
from evaluation.evaluate import load_tasks
from .common import ROOT, TARGET, config, digest, fixed_digest, validate_candidate
from .checkpoint import checkpoint
from .process_guard import run_guarded
from .opencode_worker import quota_exhausted


def find_cli():
    explicit = os.environ.get("LAB_OPENCODE_EXE")
    if explicit:
        explicit = os.path.expandvars(os.path.expanduser(explicit))
        found = shutil.which(explicit)
        if found:
            return found
        if Path(explicit).is_file():
            return str(Path(explicit).resolve())
        raise FileNotFoundError("LAB_OPENCODE_EXE must name an installed command or executable file")
    found = shutil.which("opencode")
    if found:
        return found
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        candidate = Path(os.environ["LOCALAPPDATA"]) / "Programs/ai.opencode.desktop/resources/opencode-cli.exe"
        if candidate.is_file():
            return str(candidate)
    return None


def doctor():
    errors = []
    try:
        exe = find_cli()
    except (OSError, ValueError) as exc:
        exe = None
        errors.append(str(exc))
    value = {"cli_found": bool(exe), "executable": exe, "model_configured": bool(config()["model"]),
             "runtime": runtime_info(), "errors": errors,
             "message": "Environment inspection only; no model run or credential output."}
    if exe:
        try:
            proc = subprocess.run([exe, "--version"], capture_output=True, text=True, encoding="utf-8", timeout=30)
            value["version"] = proc.stdout.strip()
            value["version_exit_code"] = proc.returncode
        except (OSError, subprocess.TimeoutExpired) as exc:
            errors.append(f"CLI version check failed: {type(exc).__name__}")
    value["numerical_tools_ready"] = all(v is not None for k, v in value["runtime"]["dependencies"].items() if k != "punpy")
    value['resource_limits']=config()['resources']
    value['hard_resource_backend']='windows-job-object' if os.name=='nt' else 'unsupported_real_execution_refused'
    print(json.dumps(value, ensure_ascii=False, indent=2))
    return value


def workspace_files(work):
    """Inventory outside permitted outputs without following task-created links."""
    work=Path(work).resolve()
    files={};links=[]
    for path in sorted(work.rglob("*")):
        relative = path.relative_to(work)
        if relative.parts[0] in {"results", "memory", ".git"} or '__pycache__' in relative.parts:
            continue
        if path.is_symlink() or path.is_junction() or not path.resolve().is_relative_to(work):
            links.append(relative.as_posix());continue
        if path.is_file():files[relative.as_posix()]=path
    return files,links


def file_fingerprint(path):
    # The task can create large files; never read an entire untrusted file into
    # the supervisor's unrestricted heap merely to check integrity.
    with path.open('rb') as stream:return __import__('hashlib').file_digest(stream,'sha256').hexdigest()


def workspace_manifest(work):
    files,links=workspace_files(work)
    if links:raise ValueError('initial workspace contains links')
    return {name:file_fingerprint(path) for name,path in files.items()}


def workspace_changes(work,before):
    files,links=workspace_files(work)
    added=sorted(set(files)-set(before))
    protected={Path(name).parts[0] for name in before if len(Path(name).parts)>1}
    return {'modified':[name for name in before if name in files and file_fingerprint(files[name])!=before[name]],
            'deleted':sorted(set(before)-set(files)),
            'added_fixed_files':[name for name in added if Path(name).parts[0] in protected],
            'unexpected_outputs':[name for name in added if Path(name).parts[0] not in protected],
            'unsafe_links':links}


def task_data_directory(task, data_root=None):
    """Resolve only explicitly supplied task inputs; never copy the suite/judge root."""
    if "data_directory" not in task["public"]:
        return None
    root = ROOT if data_root is None else Path(data_root).resolve()
    relative = Path(task["public"]["data_directory"])
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("data_directory must be project-relative and cannot contain '..'")
    source = (root/relative).resolve()
    allowed = (ROOT/"data").resolve() if root == ROOT else root
    if source == allowed or not source.is_relative_to(allowed) or not source.is_dir():
        raise ValueError("task data must name a specific input directory under the declared data root")
    return relative, source


def input_digest(tasks, data_root=None):
    h = __import__("hashlib").sha256()
    for task in tasks:
        resolved = task_data_directory(task, data_root)
        if resolved is None:
            continue
        relative, source = resolved
        for path in sorted(source.rglob("*")):
            if not path.is_file():
                continue
            if not path.resolve().is_relative_to(source):
                raise ValueError("task input link escapes its input directory")
            h.update((task["id"]+"/"+path.relative_to(source).as_posix()).encode("utf-8"))
            h.update(path.read_bytes())
    return h.hexdigest()


def task_public(task):
    public = dict(task["public"])
    instruction = public.get("instruction")
    if isinstance(instruction, str):
        # Old public cases embedded an instruction to author two files. Keep
        # their scientific requirements and leave delivery to the batch contract.
        public["instruction"] = instruction.replace(
            "输出直接放 results/，包含 report.json/report.md；数值结果保留保护位数，实际执行并引用证据。",
            "数值结果保留保护位数，实际执行并引用证据。")
    public.setdefault("required_evidence", list(task.get("expected", {}).get("evidence", [])))
    public["required_result_numbers"] = required_result_numbers(task)
    return public


def prepare_workspace(work, task, skill, data_root=None):
    for folder in [".agents", ".opencode", "labcore", "references", "templates"]:
        shutil.copytree(ROOT/folder, work/folder, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    # Runtime contracts only: evaluation/setup guides can contain answer examples
    # and do not belong in a task workspace.
    (work/"docs").mkdir()
    for name in ["CONTRACTS.md", "STRUCTURE.md"]:
        shutil.copy2(ROOT/"docs"/name, work/"docs"/name)
    shutil.copy2(ROOT/"AGENTS.md", work/"AGENTS.md")
    if Path(skill).is_dir():
        from .bundles import load, PATHS
        for name, path in load(skill).items():
            shutil.copy2(path, work/PATHS[name])
    else:
        shutil.copy2(skill, work/TARGET)
    public = task_public(task)
    if "data_directory" in public:
        input_digest([task], data_root)
        relative, source = task_data_directory(task, data_root)
        shutil.copytree(source, work/relative)
    write_json(work/"task.json", {"id": task["id"], **public})


def event_summary(stdout):
    summary = {"tool_calls": 0, "tool_errors": 0, "step_limit": False, "provider_errors": []}
    usage=[];costs=[]
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except (ValueError, TypeError):
            continue
        if not isinstance(event, dict):
            continue
        part = event.get("part", {})
        if event.get('type')=='step_finish':
            if isinstance(part.get('tokens'),dict):usage.append(part['tokens'])
            if type(part.get('cost')) in (int,float):costs.append(part['cost'])
        if event.get("sessionID"):
            summary["session_id"] = event["sessionID"]
        if event.get("type") == "error":
            error = event.get("error", {})
            if isinstance(error, dict):
                # Do not repeat provider response bodies or credentials in summaries.
                error_type = "provider_quota_exhausted" if quota_exhausted(error) else error.get("type")
                summary["provider_errors"].append({"type": error_type, "status": error.get("status")})
        if event.get("type") == "tool_use":
            summary["tool_calls"] += 1
            summary["tool_errors"] += part.get("state", {}).get("status") == "error"
        if event.get("type") == "text":
            text = part.get("text", "").lower()
            summary["step_limit"] |= any(s in text for s in
                ["最大步骤数", "最大步骤", "最大步数", "maximum number of steps", "maximum steps", "max steps reached"])
    summary['tokens']={k:sum(u.get(k,0) for u in usage) for k in ('input','output','reasoning')} if usage else None
    summary['provider_reported_cost']=sum(costs) if costs else None
    summary['billing_cost_verified']=False
    return summary


def cleanup_workspace(work):
    """Bounded deletion of exactly one generated, verified task scratch directory."""
    work=Path(work)
    if work.is_symlink() or work.is_junction() or work.resolve().parent!=Path(tempfile.gettempdir()).resolve() or not work.name.startswith('physics-lab-agent-'):
        raise ValueError('refusing cleanup outside the generated task workspace')
    for attempt in range(3):
        try:
            shutil.rmtree(work);return {'status':'removed','attempts':attempt+1}
        except FileNotFoundError:
            return {'status':'removed','attempts':attempt+1}
        except OSError as exc:
            if attempt==2:return {'status':'retained','path':str(work),'error':f'{type(exc).__name__}: {exc}'}
            time.sleep(.2*(attempt+1))


def execute_task(task,skill,task_output,log_output,prompt,exe,model,cfg,data_root,save):
    event={'task_id':task['id'],'status':'running','seconds':0.0}
    work=None;saved=False;retain=False;cancelled=False
    def state(phase):
        checkpoint(log_output/'task_state.json',{'phase':phase,**event})
    state('preparing')
    try:
        work=Path(tempfile.mkdtemp(prefix='physics-lab-agent-')).resolve()
        prepare_workspace(work,task,skill,data_root)
        before=workspace_manifest(work)
        environment=os.environ.copy()
        environment['PATH']=str(Path(sys.executable).parent)+os.pathsep+environment.get('PATH','')
        command=[sys.executable,'-u',str(ROOT/'rsi/opencode_worker.py'),'--exe',exe,
                 '--agent',cfg['agent'],'--model',model,'--prompt',prompt,
                 '--task-id',task['id'],'--model-steps',str(cfg['model_steps']),
                 '--timeout-seconds',str(cfg['timeout_seconds'])]
        state('executing')
        resources=run_guarded(command,work,environment,log_output,cfg['timeout_seconds'],cfg['resources'])
        event.update(status=resources['status'],seconds=resources['seconds'],exit_code=resources.get('exit_code'),resources=resources)
        if not resources['cleanup_verified']:
            event['status']='cleanup_failed';retain=True
        # Logs stream directly to disk; only a bounded prefix is decoded for diagnostics.
        trace=log_output/'events.jsonl'
        raw=b''
        if trace.is_file():
            with trace.open('rb') as stream:raw=stream.read(cfg['resources']['max_log_mb']*1024**2)
        summary=event_summary(raw.decode('utf-8',errors='replace'));del raw
        event['trace_summary']=summary
        if event['status'] in {'finished', 'process_failed'}:
            if any(e['type'] == 'provider_quota_exhausted' for e in summary['provider_errors']):
                event['status']='provider_quota_exhausted'
            elif summary['provider_errors']:event['status']='provider_error'
            elif summary['step_limit']:event['status']='step_limit'
        changes=workspace_changes(work,before);event['workspace_changes']=changes
        if any(changes[k] for k in ('modified','deleted','added_fixed_files','unsafe_links')):
            event['status']='fixed_files_changed';retain=True
        elif changes['unexpected_outputs']:
            event['status']='boundary_violation'
            for name in changes['unexpected_outputs']:
                target=log_output/'unexpected-outputs'/name
                target.parent.mkdir(parents=True,exist_ok=True)
                shutil.copyfile(work/name,target)
    except KeyboardInterrupt:
        event['status']='interrupted';cancelled=True
    except Exception as exc:
        event.update(status='supervisor_error',error=f'{type(exc).__name__}: {exc}')
    finally:
        try:
            state('saving')
            if work:
                for name,destination in [('results',task_output),('memory',task_output/'memory')]:
                    source=work/name
                    if source.is_dir():
                        if source.is_symlink() or source.is_junction() or any(p.is_symlink() or p.is_junction() for p in source.rglob('*')):
                            raise ValueError('task artifacts contain links; retain workspace instead of following them')
                        shutil.copytree(source,destination,dirs_exist_ok=True)
            report_path = task_output/'report.json'
            if event['status'] in {'finished', 'step_limit', 'timeout'}:
                try:
                    recovery = route_submission(task_output, task, log_output/'submission-original.json')
                    if recovery:
                        event['submission_recovery'] = recovery
                    if report_path.is_file() and render_report(report_path, task=task):
                        event['report_delivery'] = 'markdown_rendered_from_submission'
                except ValueError as exc:
                    event['report_delivery_error'] = str(exc)
            event['output_problems']=output_problems(task_output,task.get('expected',{}).get('evidence',[]))
            if 'report_delivery_error' in event:
                event['output_problems'].append(event['report_delivery_error'])
            if not event['output_problems']:
                if event['status'] == 'step_limit':
                    event.update(status='finished', completed_at_step_limit=True)
                elif event['status'] == 'timeout' and event.get('report_delivery') == 'markdown_rendered_from_submission':
                    # The worker and descendants have already stopped. The report
                    # was explicitly written within the attempt, before rendering.
                    event.update(status='finished', completed_at_timeout=True)
            if event['status']=='finished' and event['output_problems']:event['status']='incomplete_output'
            event['workspace_cleanup']={'status':'pending'}
            checkpoint(log_output/'task_event.json',event);save(event);saved=True
        except Exception as exc:
            event.update(status='artifact_save_failed',save_error=f'{type(exc).__name__}: {exc}');retain=True
        # Results and task state are durable BEFORE attempting scratch-directory deletion.
        if work and saved and not retain:
            event['workspace_cleanup']=cleanup_workspace(work)
            if event['workspace_cleanup']['status']!='removed':event['status']='cleanup_failed'
        elif work:
            event['workspace_cleanup']={'status':'retained','path':str(work),'reason':'preserve unsaved outputs or failed process cleanup'}
        checkpoint(log_output/'task_event.json',event);save(event);state('complete')
    if cancelled:raise KeyboardInterrupt
    if event['status'] in {'fixed_files_changed','supervisor_error','cleanup_failed','artifact_save_failed','resource_busy','host_memory_low','interrupted','provider_quota_exhausted'}:
        raise RuntimeError(f"task {task['id']} stopped safely: {event['status']}; records: {log_output}")
    return event


def output_problems(directory, required_evidence=None):
    """Minimum output contract, not a numerical score or execution authentication."""
    directory = Path(directory).resolve()
    problems = []
    for name in ["report.json", "report.md"]:
        p = directory/name
        if not p.is_file() or not p.resolve().is_relative_to(directory) or p.stat().st_size == 0:
            problems.append(f"missing/invalid {name}")
    try:
        report = read_json(directory/"report.json")
        if not isinstance(report, dict) or report.get("status") not in {"complete", "limited", "needs_information"}:
            problems.append("report status is invalid or failed")
        evidence = report.get("evidence") if isinstance(report, dict) else None
        if not isinstance(evidence, list):
            problems.append("report evidence must be a list")
        else:
            for name in required_evidence or []:
                if name not in evidence:problems.append(f'required evidence not listed: {name}')
            for name in evidence:
                if not isinstance(name, str):
                    problems.append("invalid evidence path")
                    continue
                p = (directory/name).resolve()
                if not p.is_relative_to(directory) or not p.is_file() or p.stat().st_size == 0:
                    problems.append(f"missing/invalid evidence: {name}")
    except (OSError, ValueError, TypeError):
        problems.append("report.json is unreadable or invalid JSON")
    return problems


def run_split(split, skill, output, execute=False, model=None, task_path=None, data_root=None, task_ids=None):
    cfg = config()
    skill = Path(skill).resolve()
    validate_candidate(skill)
    task_path = Path(task_path).resolve() if task_path else ROOT/cfg["tasks"]
    suite = read_json(task_path)
    tasks = load_tasks(split, task_path)
    if task_ids is not None:
        selected=set(task_ids)
        if not selected or selected-set(t['id'] for t in tasks):
            raise ValueError('retry task IDs must belong to this split')
        tasks=[t for t in tasks if t['id'] in selected]
    output = Path(output).resolve()
    visibility = suite.get("visibility", "public")
    if visibility not in {"public", "private"}:
        raise ValueError("suite visibility must be public or private")
    if visibility == "private" and (output == ROOT or output.is_relative_to(ROOT)):
        raise ValueError("private evaluation outputs must stay outside the public project")
    inputs_hash = input_digest(tasks, data_root)
    for task in tasks:
        resolved = task_data_directory(task, data_root)
        if resolved and (output.is_relative_to(resolved[1]) or resolved[1].is_relative_to(output)):
            raise ValueError("evaluation output must not overlap task inputs")
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(f"output directory already contains records: {output}; keep them and use a fresh run output directory (e.g. {output.name}-002)")
    selected_model = model or cfg["model"]
    exe = find_cli()
    if execute and (not exe or not selected_model):
        raise ValueError("real execution requires an installed CLI and explicit --model provider/model")
    cli_version=None
    if execute:
        version_proc=subprocess.run([exe,'--version'],capture_output=True,text=True,encoding='utf-8',timeout=30)
        if version_proc.returncode or not (version_proc.stdout or version_proc.stderr).strip():
            raise RuntimeError('cannot record the harness version; no model task was started')
        cli_version=(version_proc.stdout or version_proc.stderr).strip()
    output.mkdir(parents=True, exist_ok=True)
    from .bundles import PATHS, hashes
    bundle_mode = skill.is_dir()
    manifest = {"split": split, "mode": "opencode" if execute else "prepared_only", "executed": execute,
                "skill_hash": digest(skill), "fixed_hash": fixed_digest(PATHS.values() if bundle_mode else None), "model": selected_model,
                "model_steps": cfg["model_steps"], "timeout_seconds": cfg["timeout_seconds"],
                "runtime": runtime_info(), "tasks": []}
    manifest.update(visibility=visibility, taskset_hash=digest(task_path), inputs_hash=inputs_hash)
    if bundle_mode:
        manifest.update(candidate_kind='three-skill-bundle-v1', skill_hashes=hashes(skill))
    manifest['resource_limits']=cfg['resources']
    if task_ids is not None:manifest['retry_task_ids']=list(task_ids)
    manifest['harness']={'name':'opencode','version':cli_version,'transport':'private-api-v2'}
    checkpoint(output/'run_manifest.json',manifest)
    for task in tasks:
        task_output = output/task["id"]
        task_output.mkdir()
        log_output = output/"_runner"/task["id"]
        log_output.mkdir(parents=True)
        prompt = task_prompt(task, cfg)
        write_json(log_output/"request.json", {"task_id": task["id"], "public": task_public(task), "prompt": prompt})
        event = {"task_id": task["id"], "status": "prepared_only", "seconds": 0.0}
        if execute:
            print(f"Starting {task['id']} (memory {cfg['resources']['memory_limit_mb']} MiB, timeout {cfg['timeout_seconds']}s)",flush=True)
            def save(event):
                checkpoint(output/'run_manifest.json',{**manifest,'tasks':manifest['tasks']+[event]})
            event=execute_task(task,skill,task_output,log_output,prompt,exe,selected_model,cfg,data_root,save)
        manifest["tasks"].append(event)
        checkpoint(output/"run_manifest.json", manifest)
        if execute:
            print(f"Saved {task['id']}: {event['status']}, {event['seconds']:.1f}s; process tree exited={event.get('resources',{}).get('cleanup_verified')}",flush=True)
    return manifest


def task_prompt(task, cfg):
    return (
        f"读取 task.json，按 AGENTS.md 处理隔离工作区的任务 {task['id']}。\n"
        "1. 按需加载 Skill，读取本题输入。只处理题目要求的计算、必要核验和证据。\n"
        "2. 在项目根目录运行形如 python .agents/skills/<skill>/scripts/<script>.py <输入> --out results/<证据>.json "
        "的单条命令。自编驱动统一用 python results/analysis.py，正斜杠和裸 python 即可。"
        "用读取工具查看文件，命令中不用 PowerShell、.venv 路径、python -c、管道或重定向。"
        "蒙特卡洛按题设执行抽样，记录种子、样本、批次及均值与端点诊断。"
        "没有要求时省略解析卷积、KDE、重抽样。两次运行同一命令后，先用现有证据交付或说明真实阻断原因。"
        "拟合脚本已经给出后端、协方差与诊断。ODR 使用 diagnostics.x_adjustments 和 y_adjustments，"
        "按各轴 u 标准化后计算目标；有充分证据即提交。"
        "脚本确实不支持时，用一个短的 analysis.py 实际计算和核验。\n"
        "3. 计算、必要核验、修约展示和 task.json.required_evidence 齐全时，"
        "下一动作就写 results/report.json，随后结束本题。"
        "提交前停止可选二次验证、额外分析或重复读文件。"
        "本批次由运行器校验 JSON 并生成 report.md，提交后无需调用渲染命令或写正文文件。"
        "直接在 results/ 写文件，使用 task.json 的 id 作为 task_id。"
        "evidence 列出真实的相对文件名；required_result_numbers 填未修约 JSON 数字。"
        "sections 的 data/model/method/results/reproduction 各用简短文字说明。"
        "核对结果数值和单位来自同一计算产物，换单位要同时换数值与 u。"
        "issues 中题目给出的 ID 按原字符串单独列项，解释写 sections.results。"
        "rounding_file 只填实际 round_report.py 输出的 JSON，区间端点、因子、单个 u 不构造成 ± 配对。"
        "证书给 U 与 k 时用 U/k；覆盖概率与分布仅在已有依据时说明。"
        "缺信息时提交 needs_information、result:null、issues 和题目所需证据。"
        "题目要求说明结果不存在时，按题目状态提交 result:null、issues 和依据。\n"
        "固定输入和 Skill 只读。"
        f"本题进程树内存上限 {cfg['resources']['memory_limit_mb']} MiB，75% 仅记录预警。"
        "数值库单线程，先估算内存，采用有界分块计算。"
    )


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--doctor", action="store_true")
    p.add_argument("--split", choices=["train", "selection", "test"])
    p.add_argument("--skill", default=str(ROOT/TARGET))
    p.add_argument("--out")
    p.add_argument("--model")
    p.add_argument("--tasks", help="explicit public or external private task JSON; overrides this run only")
    p.add_argument("--data-root", help="explicit root for task input directories; default is the project")
    p.add_argument("--execute", action="store_true", help="actually run OpenCode; may incur provider costs")
    a = p.parse_args(argv)
    if a.doctor:
        doctor()
    elif not a.split or not a.out:
        p.error("provide --split and --out, or --doctor")
    else:
        try:
            result = run_split(a.split, a.skill, a.out, a.execute, a.model, a.tasks, a.data_root)
        except (FileExistsError, ValueError) as exc:
            p.error(str(exc))
        counts = Counter(t["status"] for t in result["tasks"])
        print(f"{result['mode']}: {dict(counts)}; {a.out}")
        if a.execute:
            for task in result["tasks"]:
                if task["status"] != "finished":
                    print(f"  {task['task_id']}: {task['status']}; {task.get('output_problems', [])}; logs: {a.out}/_runner/{task['task_id']}")
            print("Report generation is checked here; run evaluation.evaluate for task correctness.")
            return 0 if all(t["status"] == "finished" for t in result["tasks"]) else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
