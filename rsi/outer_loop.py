import argparse
import csv
import json
from pathlib import Path
import shutil
from labcore.io import read_json, write_json
from labcore.project import runtime_info
from evaluation.evaluate import evaluate
from .common import ROOT, TARGET, config, digest, fixed_digest, validate_candidate

EPSILON = 1e-12


def _rate(task, rate_key, pass_key):
    value = task.get(rate_key)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if pass_key in task:
        return 1.0 if task[pass_key] is True else 0.0
    return None


def _observation_count(result):
    tasks = result.get("tasks", [])
    repetitions = result.get("repetition_count")
    if not isinstance(repetitions, int) or repetitions <= 0:
        lengths = [len(task.get("repetitions", [])) for task in tasks]
        repetitions = max(lengths, default=1)
    return max(1, len(tasks) * repetitions)


def decision(baseline, candidate, minimum_gain=0.0, max_regression=3.0,
             max_task_pass_rate_regression=1/3,
             overall_observation_regression_allowance=1):
    if baseline.get('review_pending_count', 0) or candidate.get('review_pending_count', 0):
        raise ValueError('complete scientific reviews before selection')
    previous = {r["task_id"]: r for r in baseline["tasks"]}
    current = {r["task_id"]: r for r in candidate["tasks"]}
    if previous.keys() != current.keys():
        raise ValueError("selection task sets must match")
    regressions = []
    details = {}
    for key in previous:
        reasons = []
        if current[key]["score"] < previous[key]["score"] - max_regression - EPSILON:
            reasons.append("task_score")
        for label, rate_key, pass_key in (
            ("strict_success", "success_rate", "task_passed"),
            ("critical_gate", "gate_pass_rate", "gate_passed"),
        ):
            before = _rate(previous[key], rate_key, pass_key)
            after = _rate(current[key], rate_key, pass_key)
            if before is not None and after is not None and \
                    after < before - max_task_pass_rate_regression - EPSILON:
                reasons.append(label)
        if reasons:
            regressions.append(key)
            details[key] = reasons
    observation_allowance = overall_observation_regression_allowance / _observation_count(candidate)
    global_reasons = []
    for label, key in (
        ("overall_strict_success", "task_success_rate"),
        ("evidence_compliance", "evidence_compliance_rate"),
        ("format_compliance", "format_compliance_rate"),
    ):
        before = baseline.get(key)
        after = candidate.get(key)
        if isinstance(before, (int, float)) and not isinstance(before, bool) and \
                isinstance(after, (int, float)) and not isinstance(after, bool) and \
                after < before - observation_allowance - EPSILON:
            global_reasons.append(label)
    gain = candidate["mean_score"]-baseline["mean_score"]
    accepted = gain > minimum_gain and not regressions and not global_reasons
    return {"accepted": accepted, "gain": gain, "regressions": regressions,
            "regression_details": details, "global_regressions": global_reasons,
            "reason": "accepted" if accepted else "task_regression" if regressions or global_reasons else "insufficient_gain",
            "policy": {"minimum_gain": minimum_gain,
                       "task_score_regression_limit": max_regression,
                       "task_pass_rate_regression_limit": max_task_pass_rate_regression,
                       "overall_observation_regression_allowance": overall_observation_regression_allowance,
                       "one_observation_rate_allowance": observation_allowance}}


def checked_evaluation(split, reports, skill_hash, required_model=None, required_harness=None):
    manifest = read_json(Path(reports)/"run_manifest.json")
    cfg = config()
    if manifest.get("executed") is not True or manifest.get("mode") != "opencode":
        raise ValueError("prepared/example reports cannot be used as actual RSI measurements")
    from .bundles import PATHS, FORMAT
    is_bundle = manifest.get('candidate_kind') == FORMAT
    if manifest.get("split") != split or manifest.get("skill_hash") != skill_hash or manifest.get("fixed_hash") != fixed_digest(PATHS.values() if is_bundle else None):
        raise ValueError("manifest split/skill/fixed environment mismatch")
    if manifest.get("visibility", "public") != "public" or manifest.get("taskset_hash") != digest(ROOT/cfg["tasks"]):
        raise ValueError("RSI selection requires its frozen public development suite; private evaluation is held out")
    from .runner import input_digest
    from evaluation.evaluate import load_tasks
    if manifest.get('inputs_hash')!=input_digest(load_tasks(split,ROOT/cfg['tasks'])):
        raise ValueError('frozen task input hash mismatch')
    if manifest.get("runtime") != runtime_info():
        raise ValueError("Python or numerical dependency versions changed; start a consistent experiment")
    harness=manifest.get('harness',{})
    if harness.get('name')!='opencode' or not harness.get('version') or (required_harness and harness!=required_harness):
        raise ValueError('harness version must be recorded and held fixed')
    if not manifest.get("model") or (required_model and manifest["model"] != required_model):
        raise ValueError("model must be declared and held fixed")
    if manifest.get("model_steps") != cfg["model_steps"] or manifest.get("timeout_seconds") != cfg["timeout_seconds"]:
        raise ValueError("budget mismatch")
    if manifest.get('resource_limits')!=cfg['resources']:raise ValueError('resource budget mismatch')
    result = evaluate(split, reports, ROOT/cfg["tasks"])
    if result.get('review_pending_count', 0):
        raise ValueError('scientific reviews are pending or stale; complete operator reviews before RSI')
    result['harness']=harness
    events = {e["task_id"]: e for e in manifest["tasks"]}
    from .preflight import protocol
    rules=protocol()
    for task in result["tasks"]:
        if task["task_id"] not in events:
            raise ValueError("manifest is missing a task")
        resources=events[task['task_id']].get('resources',{})
        if resources.get('cleanup_verified') is not True or resources.get('active_processes_after')!=0 or resources.get('limits')!=cfg['resources']:
            raise ValueError('task process-tree cleanup/resource containment is not verified')
        if events[task["task_id"]]["status"] != "finished":
            status=events[task['task_id']]['status']
            if status in set(rules['integrity_failure_statuses'])|set(rules['retry_statuses']):
                raise ValueError('run infrastructure/integrity failure requires diagnosis before RSI; preserve logs and retry under the same protocol')
            if status not in set(rules['budget_failure_statuses'])|set(rules.get('task_failure_statuses',[])):
                raise ValueError('unknown task status; cannot treat unclassified infrastructure errors as scored failures')
            task.update(score=0.0, gate_passed=False, task_passed=False, correctness_rate=0.0,
                        evidence_compliance_rate=0.0, format_compliance_rate=0.0,
                        runner_status=events[task["task_id"]]["status"])
            if 'dimension_rates' in task:task['dimension_rates']={k:0 for k in task['dimension_rates']}
    result["mean_score"] = sum(t["score"] for t in result["tasks"])/len(result["tasks"])
    result["task_success_rate"] = sum(t["task_passed"] for t in result["tasks"])/len(result["tasks"])
    for metric in ("evidence_compliance_rate", "format_compliance_rate"):
        result[metric] = sum(t[metric] for t in result["tasks"])/len(result["tasks"])
    specs={t['id']:t for t in load_tasks(split,ROOT/cfg['tasks'])};groups={}
    for task in result['tasks']:
        for cap in specs[task['task_id']].get('capabilities',[]):groups.setdefault(cap,[]).append(task)
    result['by_capability']={k:{'tasks':len(rows),'task_success_rate':sum(t['task_passed'] for t in rows)/len(rows),
                               'mean_score':sum(t['score'] for t in rows)/len(rows)} for k,rows in groups.items()}
    return result, manifest["model"]


def log_event(run, event):
    with (run/"history.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(event, ensure_ascii=False, allow_nan=False)+"\n")
    curve = run/"curve.csv"
    fresh = not curve.exists()
    with curve.open("a", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["round", "train", "selection", "accepted", "gain"])
        if fresh:
            writer.writeheader()
        writer.writerow({k: event[k] for k in writer.fieldnames})


def initialize(run, train_reports, selection_reports, history=None):
    if run.exists():
        raise FileExistsError("use a fresh RSI run directory")
    skill_hash = digest(ROOT/TARGET)
    from .repeated import checked_repeated
    train, model = checked_repeated("train", train_reports, skill_hash)
    selection, _ = checked_repeated("selection", selection_reports, skill_hash, model,train['harness'])
    if train['freeze_sha256']!=selection['freeze_sha256']:raise ValueError('train and selection must use the same freeze record')
    run.mkdir(parents=True)
    (run/"v0").mkdir()
    shutil.copy2(ROOT/TARGET, run/"v0/SKILL.md")
    write_json(run/"v0/train.json", train)
    write_json(run/"v0/selection.json", selection)
    state = {"round": 0, "incumbent_file": "v0/SKILL.md", "training_feedback": "v0/train.json",
             "selection_file": "v0/selection.json", "model": model, "harness":train['harness'], "fixed_hash": fixed_digest(),
             'freeze_sha256':train['freeze_sha256']}
    write_json(run/"state.json", state)
    log_event(run, {"round": 0, "train": train["mean_score"], "selection": selection["mean_score"],
                    "accepted": True, "gain": 0.0, "note": "measured baseline"})
    from .versioning import checkpoint
    commit=checkpoint(run,'v0',train_reports,selection_reports,history)
    write_json(run/'v0/git-record.json',{'commit':commit,'journal':'outside the public project; local-only'})


def consider(run, candidate, train_reports, selection_reports, history=None):
    state = read_json(run/"state.json")
    if state["fixed_hash"] != fixed_digest():
        raise ValueError("fixed project changed after baseline; start a new experiment")
    cfg = config()
    if state["round"] >= cfg["rounds"]:
        raise ValueError("configured round budget already completed")
    validate_candidate(candidate)
    candidate_hash = digest(candidate)
    if candidate_hash == digest(run/state["incumbent_file"]):
        raise ValueError("candidate is unchanged; not a new proposal")
    from .repeated import checked_repeated
    train, _ = checked_repeated("train", train_reports, candidate_hash, state["model"],state['harness'])
    selection, _ = checked_repeated("selection", selection_reports, candidate_hash, state["model"],state['harness'])
    if train['freeze_sha256']!=state['freeze_sha256'] or selection['freeze_sha256']!=state['freeze_sha256']:
        raise ValueError('candidate must use the original experiment freeze')
    outcome = decision(read_json(run/state["selection_file"]), selection,
                       cfg["min_score_gain"], cfg["max_task_regression"],
                       cfg["max_task_pass_rate_regression"],
                       cfg["overall_observation_regression_allowance"])
    step = state["round"]+1
    version = f"v{step}"
    folder = run/version
    folder.mkdir()
    shutil.copy2(candidate, folder/"SKILL.md")
    write_json(folder/"train.json", train)
    write_json(folder/"selection.json", selection)
    import difflib
    diff = "".join(difflib.unified_diff((run/state["incumbent_file"]).read_text(encoding="utf-8").splitlines(True),
                                       Path(candidate).read_text(encoding="utf-8").splitlines(True),
                                       fromfile="incumbent", tofile=version))
    (folder/"diff.patch").write_text(diff, encoding="utf-8")
    if outcome["accepted"]:
        state.update(incumbent_file=f"{version}/SKILL.md", training_feedback=f"{version}/train.json",
                     selection_file=f"{version}/selection.json")
    state["round"] = step
    event = {"round": step, "train": train["mean_score"], "selection": selection["mean_score"], **outcome}
    write_json(folder/"decision.json", event)
    log_event(run, event)
    write_json(run/"state.json", state)
    from .versioning import checkpoint
    commit=checkpoint(run,version,train_reports,selection_reports,history)
    write_json(folder/'git-record.json',{'commit':commit,'journal':'outside the public project; local-only'})
    return event


def plan():
    cfg = config()
    from .bundles import SKILLS
    print(json.dumps({"status": "plan_only", "rounds": cfg["rounds"], "legacy_single_target": TARGET,
                      "current_entrypoint":"python -m rsi.campaign", "targets":list(SKILLS),
                      "model": cfg["model"], "steps": ["freeze bundle protocol; measure v0 or explicitly validate baseline inheritance",
                      "export fresh-context train-only proposal packet", "save actual candidate response and semantic review", "run full candidate train and selection",
                      f"accept/reject and snapshot, repeat up to {cfg['rounds']} rounds", "evaluate chosen version once on final test"],
                      "no_measurements_generated": True}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--plan", action="store_true")
    p.add_argument("--init", action="store_true")
    p.add_argument("--run")
    p.add_argument("--candidate")
    p.add_argument("--train-reports")
    p.add_argument("--selection-reports")
    p.add_argument("--history", help="local-only Git journal outside the public project; default ../evaluation-history")
    a = p.parse_args()
    if a.plan:
        plan()
    elif not a.run or not a.train_reports or not a.selection_reports:
        p.error("require --run, --train-reports, --selection-reports")
    elif a.init:
        initialize(Path(a.run), a.train_reports, a.selection_reports,a.history)
        print("measured baseline initialized")
    elif a.candidate:
        print(json.dumps(consider(Path(a.run), a.candidate, a.train_reports, a.selection_reports,a.history), ensure_ascii=False))
    else:
        p.error("use --plan, --init or --candidate")
