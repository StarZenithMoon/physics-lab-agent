import argparse
import csv
import hashlib
import math
import re
from pathlib import Path
from labcore.io import read_json, write_json
from labcore.project import ROOT
from .rubric import apply_rubric

SECTIONS = ["数据与任务", "测量模型与假设", "结果与局限", "复现与来源"]


def load_scoring():
    weights = read_json(ROOT / "evaluation/scoring.json")["weights"]
    if set(weights) != {"correctness", "evidence", "format"} or any(
            isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0
            for v in weights.values()) or not math.isclose(sum(weights.values()), 100):
        raise ValueError("diagnostic scoring weights must be nonnegative and sum to 100")
    return weights


def load_tasks(split, path=None):
    tasks = read_json(path or ROOT / "evaluation/public-cases.json")["tasks"]
    ids = [t["id"] for t in tasks]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate task IDs")
    selected = [t for t in tasks if t["split"] == split]
    if not selected:
        raise ValueError(f"empty/unknown split: {split}")
    for task in selected:
        if not isinstance(task["id"], str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", task["id"]):
            raise ValueError("task ID must use portable letters, digits, underscores or hyphens")
    return selected


def field(obj, path):
    for key in path.split("."):
        obj = obj[key]
    return obj


def assertion(report, check):
    try:
        actual = field(report, check["path"])
        if "equals" in check:
            return actual == check["equals"]
        if "contains" in check:
            return isinstance(actual, list) and check["contains"] in actual
        if isinstance(actual, bool) or not isinstance(actual, (int, float)) or not math.isfinite(actual):
            return False
        if "target" in check:
            return abs(actual-check["target"]) <= check.get("atol", 0)+check.get("rtol", 0)*abs(check["target"])
        return check.get("min", -math.inf) <= actual <= check.get("max", math.inf)
    except (KeyError, TypeError, ValueError):
        return False


def evidence_ok(directory, filename, report):
    if filename not in report.get("evidence", []):
        return False
    path = (directory / filename).resolve()
    if not path.is_relative_to(directory.resolve()) or not path.is_file() or path.stat().st_size == 0:
        return False
    try:
        if path.suffix == ".json":
            value = read_json(path)
            if not isinstance(value, dict) or not value:
                return False
            if filename == "calculation.json":
                # Minimum contract checks; not an independent recomputation.
                a, m = value["analytic"], value["mc"]
                for u in (a["u"], m["u"]):
                    if isinstance(u, bool) or not isinstance(u, (int, float)) or not math.isfinite(u) or u < 0:
                        return False
                if m.get("invalid_samples") != 0 or m.get("samples", 0) < 1000:
                    return False
            if filename == "model.json" and not value.get("model_id"):
                return False
        elif path.suffix == ".csv":
            with path.open(encoding="utf-8-sig", newline="") as stream:
                reader = csv.DictReader(stream)
                if not reader.fieldnames or not list(reader):
                    return False
        return True
    except (OSError, ValueError, TypeError, KeyError):
        return False


def manual_review(task, directory, review):
    criteria = task['expected'].get('manual_checks', [])
    if not criteria:
        return []
    valid = isinstance(review, dict) and review.get('task_id') == task['id'] and bool(review.get('reviewer'))
    if valid:
        valid = review.get('report_sha256') == hashlib.sha256((directory/'report.json').read_bytes()).hexdigest()
    decisions = review.get('checks', {}) if valid else {}
    return [{'id': c['id'], 'passed': decisions.get(c['id'], {}).get('passed') is True and
             bool(decisions.get(c['id'], {}).get('reason', '').strip()),
             'review_status': 'recorded' if valid else 'pending_or_stale'} for c in criteria]


def score_task(task, directory, weights=None, review=None):
    directory = Path(directory)
    weights = load_scoring() if weights is None else weights
    try:
        report_path = directory / "report.json"
        if not report_path.resolve().is_relative_to(directory.resolve()):
            raise ValueError("report.json escapes report directory")
        report = read_json(report_path)
        if not isinstance(report, dict):
            raise ValueError("report.json must be an object")
        checks = task["expected"]["checks"]
        numeric = [{"path": c["path"], "passed": bool(assertion(report, c)),
                    "critical": bool(c.get("critical", False))} for c in checks]
        evidence = [{"file": f, "passed": evidence_ok(directory, f, report)} for f in task["expected"]["evidence"]]
        md_path = directory / "report.md"
        if not md_path.resolve().is_relative_to(directory.resolve()):
            raise ValueError("report.md escapes report directory")
        md = md_path.read_text(encoding="utf-8") if md_path.exists() else ""
        headings = {line.strip()[3:].strip() for line in md.splitlines() if line.startswith("## ")}
        formatting = [{"section": s, "passed": s in headings} for s in SECTIONS]
        manual = manual_review(task, directory, review)
        ratio = lambda items: sum(i["passed"] for i in items)/len(items) if items else 1.0
        correctness_rate, evidence_rate, format_rate = ratio(numeric+manual), ratio(evidence), ratio(formatting)
        score = weights["correctness"]*correctness_rate + weights["evidence"]*evidence_rate + weights["format"]*format_rate
        gate = all(i["passed"] for i in numeric if i["critical"]) and all(i["passed"] for i in evidence+manual)
        result = {"task_id": task["id"], "score": score, "gate_passed": gate,
                "task_passed": bool(numeric and all(i["passed"] for i in numeric+evidence+manual) and md.strip()),
                "correctness_rate": correctness_rate, "evidence_compliance_rate": evidence_rate,
                "format_compliance_rate": format_rate,
                "checks": numeric, "manual_checks": manual, "evidence": evidence, "format": formatting}
    except (OSError, ValueError, TypeError, KeyError) as exc:
        failure = {"task_id": task["id"], "score": 0.0, "gate_passed": False, "task_passed": False,
                "correctness_rate": 0.0, "evidence_compliance_rate": 0.0, "format_compliance_rate": 0.0, "error": str(exc)}
        if 'rubric' in task['expected']:
            from .rubric import DIMENSIONS, VERSION
            failure.update(rubric_version=VERSION,score_status='automatic_failure',review_complete=True,
                           dimension_rates={k:0 for k in DIMENSIONS})
        return failure
    # Broken rubric/configuration/review machinery is an operator error, not an Agent verdict.
    return apply_rubric(task, directory, result, review)


def evaluate(split, reports, task_path=None):
    tasks = load_tasks(split, task_path)
    weights = load_scoring()
    results = []
    for task in tasks:
        review_path = Path(reports)/'_reviews'/f"{task['id']}.json"
        # Reviews are operator records outside the individual Agent output directory.
        try:
            review = read_json(review_path) if review_path.is_file() else None
        except (ValueError, OSError):
            review = None
        results.append(score_task(task, Path(reports)/task['id'], weights, review))
    scientific = any("rubric" in t["expected"] for t in tasks)
    pending = sum(r.get("score_status") == "pending_review" for r in results)
    groups={}
    for task,result in zip(tasks,results):
        for capability in task.get('capabilities',[]):groups.setdefault(capability,[]).append(result)
    return {"split": split, "mean_score": sum(r["score"] for r in results)/len(results),
            "review_pending_count": pending, "score_status": "pending_review" if pending else "reviewed" if scientific else "automatic_diagnostic",
            "by_capability":{k:{'tasks':len(rows),'task_success_rate':sum(r['task_passed'] for r in rows)/len(rows),
                                'mean_score':sum(r['score'] for r in rows)/len(rows)} for k,rows in groups.items()},
            "task_success_rate": sum(r["task_passed"] for r in results)/len(results),
            "evidence_compliance_rate": sum(r["evidence_compliance_rate"] for r in results)/len(results),
            "format_compliance_rate": sum(r["format_compliance_rate"] for r in results)/len(results),
            "tasks": results, "weights": read_json(ROOT / "evaluation/scoring.json")["scientific_weights"] if scientific else weights,
            "score_role": "scientific-rubric-v1" if scientific else "development_diagnostic",
            "limitations": ["Evidence presence is checked, not authenticated execution traces.",
                            "Tasks with manual_checks require an operator review bound to the exact report hash."]}


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--split", choices=["train", "selection", "test"], required=True)
    p.add_argument("--reports", required=True)
    p.add_argument("--tasks")
    p.add_argument("--out", required=True)
    args = p.parse_args()
    value = evaluate(args.split, args.reports, args.tasks)
    write_json(args.out, value)
    print(f"{args.split}: {value['mean_score']:.2f} / 100 ({len(value['tasks'])} tasks)")
