"""Promote a measured, policy-accepted candidate into a new RSI baseline.

No model calls are made. The selected reports are replayed with the current
deterministic evaluator and frozen with the installed Skill bundle.
"""
import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import shutil

from labcore.io import read_json
from labcore.project import ROOT
from evaluation.evaluate import evaluate
from . import bundles
from .baseline_migration import score_signature
from .checkpoint import checkpoint
from .common import digest
from .preflight import identity, protocol
from .repeated import aggregate
from .versioning import file_hash, git, zip_reports
from tools.package_source import package


def utc():
    return datetime.now(timezone.utc).isoformat()


def replay_tree(root, split, expected):
    values = []
    for repetition in sorted(Path(root).glob("rep-*")):
        reports = repetition / "reports"
        if not reports.is_dir():
            raise ValueError(f"missing effective reports for {split} {repetition.name}")
        value = evaluate(split, reports)
        if value["review_pending_count"]:
            raise ValueError(f"{split} {repetition.name} has pending or stale reviews")
        values.append(value)
    actual = aggregate(values)
    if score_signature(actual) != score_signature(expected):
        raise ValueError(f"{split} replay differs from the selected candidate score")
    return actual


def selected_decision(run, version):
    from .campaign import settings
    from .outer_loop import decision
    policy = settings()
    previous = read_json(run / "v0/selection.json")
    selected = read_json(run / version / "selection.json")
    return decision(
        previous,
        selected,
        policy["selection_min_gain"],
        policy["max_per_task_regression"],
        policy["max_per_task_pass_rate_regression"],
        policy["overall_observation_regression_allowance"],
    )


def freeze(run, version, output, history):
    run = Path(run).resolve()
    version = version.lower()
    output = Path(output).resolve()
    history = Path(history).resolve()
    if run == ROOT or not run.is_relative_to(ROOT) or not run.is_dir():
        raise ValueError("campaign run must be an existing directory inside the project")
    if not (run / "state.json").is_file() or not (run / version / "decision.json").is_file():
        raise ValueError("campaign or selected generation is missing")
    if output.exists():
        raise FileExistsError("preserve the existing baseline freeze")
    if not output.is_relative_to(history) or history == output:
        raise ValueError("promotion freeze must be stored inside the local evaluation history")
    if history == ROOT or history.is_relative_to(ROOT) or ROOT.is_relative_to(history):
        raise ValueError("RSI measurement history must remain outside the public project")
    if git(history, "remote"):
        raise ValueError("RSI measurement history must remain local-only")
    if git(history, "status", "--porcelain"):
        raise ValueError("commit all prior evaluation-history work before freezing")
    if git(ROOT, "status", "--porcelain"):
        raise ValueError("commit reviewed project source before freezing")

    candidate_bundle = run / version / "bundle"
    candidate_hash = bundles.digest(candidate_bundle)
    current_identity = identity(bundle_mode=True)
    if current_identity["v0_skill_hash"] != candidate_hash:
        raise ValueError("installed Skill bundle does not equal the selected candidate")
    base_freeze = read_json(run / "freeze-record.json")
    base_identity = base_freeze["identity"]
    differences = {key for key in set(base_identity) | set(current_identity)
                   if base_identity.get(key) != current_identity.get(key)}
    if not differences <= {"fixed_hash", "v0_skill_hash"}:
        raise ValueError(f"unexpected measurement-condition changes: {sorted(differences)}")

    train = read_json(run / version / "train.json")
    selection = read_json(run / version / "selection.json")
    for split, score in (("train", train), ("selection", selection)):
        if score.get("review_pending_count") or score.get("repetition_count") != 3:
            raise ValueError(f"{split} selected score is incomplete")
    train_replay = replay_tree(run / "evaluations" / version / "train", "train", train)
    selection_replay = replay_tree(run / "evaluations" / version / "selection", "selection", selection)
    old_decision = read_json(run / version / "decision.json")
    new_decision = selected_decision(run, version)
    if old_decision.get("accepted") is not False or new_decision.get("accepted") is not True:
        raise ValueError("promotion requires a preserved old reject and an accepted policy reassessment")

    temporary = output.with_name("." + output.name + ".tmp")
    if temporary.exists():
        raise FileExistsError("preserve incomplete promotion staging directory")
    temporary.mkdir(parents=True)
    try:
        installed = bundles.create(temporary / "bundle")
        if bundles.digest(installed) != candidate_hash:
            raise ValueError("copied installed bundle differs from the selected candidate")
        zip_reports(run / "evaluations" / version / "train", temporary / "train-reports.zip")
        zip_reports(run / "evaluations" / version / "selection", temporary / "selection-reports.zip")
        checkpoint(temporary / "train-score.json", train)
        checkpoint(temporary / "selection-score.json", selection)
        package(temporary / "source.zip")
        promotion = {
            "kind": "rsi_selected_baseline_promotion_v1",
            "created_at_utc": utc(),
            "new_model_calls_during_promotion": 0,
            "source_commit": git(ROOT, "rev-parse", "HEAD"),
            "campaign": str(run),
            "selected_version": version,
            "candidate_hash": candidate_hash,
            "measurement_freeze_sha256": digest(run / "freeze-record.json"),
            "measurement_identity": base_identity,
            "new_identity": current_identity,
            "identity_differences": sorted(differences),
            "old_decision": old_decision,
            "policy_reassessment": new_decision,
            "policy_authority": "Current frozen RSI policy: accept any positive selection gain and allow at most a 3-point per-task mean regression.",
            "train_replay": {
                "mean_score": train_replay["mean_score"],
                "task_success_rate": train_replay["task_success_rate"],
                "review_pending_count": train_replay["review_pending_count"],
            },
            "selection_replay": {
                "mean_score": selection_replay["mean_score"],
                "task_success_rate": selection_replay["task_success_rate"],
                "review_pending_count": selection_replay["review_pending_count"],
            },
            "checks": [
                "installed Skill bundle equals the selected candidate bundle",
                "current identity differs from the measured base only in fixed_hash and v0_skill_hash",
                "all three repetitions replay to the selected archived scores",
                "all scientific reviews are complete and bound to current artifacts",
                "current accepted policy is recorded",
                "promotion itself made zero model calls",
            ],
            "limitations": [
                "The 54 v3 task sessions are replayed under the frozen current decision policy without new model calls.",
                "Public test and private final acceptance were not run during promotion.",
            ],
        }
        checkpoint(temporary / "promotion.json", promotion)
        freeze_record = {
            "kind": "frozen_pre_v0_protocol",
            "created_at_utc": utc(),
            "formal_baseline_executed": True,
            "git_commit": promotion["source_commit"],
            "identity": current_identity,
            "source_archive_sha256": file_hash(temporary / "source.zip"),
            "promotion_sha256": file_hash(temporary / "promotion.json"),
            "protocol": protocol(),
            "purpose": "selected v3 baseline under user-revised RSI policy",
        }
        checkpoint(temporary / "freeze.json", freeze_record)
        files = {path.relative_to(temporary).as_posix(): file_hash(path)
                 for path in temporary.rglob("*")
                 if path.is_file() and path.name != "checkpoint.json"}
        checkpoint(temporary / "checkpoint.json", {
            "kind": "rsi_selected_baseline_promotion_archive_v1",
            "file_count": len(files),
            "files": files,
        })
        os.rename(temporary, output)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    relative = output.relative_to(history).as_posix()
    git(history, "add", "--", relative)
    git(history, "-c", "user.name=Physics Lab Evaluator", "-c", "user.email=physics-lab@localhost",
        "-c", "core.hooksPath=", "commit", "-m",
        "Promote v3 to RSI baseline under user-revised policy", "--only", "--", relative)
    commit = git(history, "log", "-1", "--format=%H", "--", relative)
    if not commit or git(history, "status", "--porcelain"):
        raise RuntimeError("v3 baseline promotion archive was not committed cleanly")
    return {
        "output": str(output),
        "git_commit": commit,
        "freeze_sha256": file_hash(output / "freeze.json"),
        "skill_hash": candidate_hash,
        "old_decision": old_decision,
        "new_decision": new_decision,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--history", required=True)
    args = parser.parse_args()
    result = freeze(args.run, args.version, args.out, args.history)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
