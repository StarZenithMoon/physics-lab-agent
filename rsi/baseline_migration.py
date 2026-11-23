"""Freeze an already measured bundle after a policy-only RSI change.

This command never calls a model. It verifies immutable report archives,
replays the deterministic evaluator, binds the measurements to the current
policy identity, and creates a new campaign with the inherited baseline.
"""
import argparse
import csv
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from zipfile import ZipFile

from labcore.io import read_json, write_json
from labcore.project import ROOT, runtime_info
from tools.package_source import package
from evaluation.evaluate import evaluate, load_tasks
from . import bundles
from .bridge import extract
from .checkpoint import checkpoint
from .common import digest, fixed_digest
from .preflight import identity, protocol, verify
from .repeated import aggregate
from .versioning import file_hash, git


def utc():
    return datetime.now(timezone.utc).isoformat()


def checked_file(root, relative):
    path = (Path(root) / relative).resolve()
    if not path.is_relative_to(Path(root).resolve()) or not path.is_file():
        raise ValueError(f"missing migration input: {relative}")
    return path


def verify_archive_checkpoint(root):
    root = Path(root).resolve()
    record = read_json(root / "checkpoint.json")
    files = record.get("files")
    if not isinstance(files, dict) or not files:
        raise ValueError("archive checkpoint has no file manifest")
    actual = {path.relative_to(root).as_posix() for path in root.rglob("*")
              if path.is_file() and path.name != "checkpoint.json"}
    if actual != set(files):
        raise ValueError("archive is partial or contains unrecorded files")
    for relative, expected in files.items():
        path = checked_file(root, relative)
        if file_hash(path) != expected:
            raise ValueError(f"archive hash mismatch: {relative}")
    return record


def score_signature(value):
    """Compare deterministic scoring while preserving archived run statistics."""
    tasks = []
    for task in value["tasks"]:
        row = {key: task.get(key) for key in
               ("task_id", "score", "score_sd", "task_passed", "gate_passed",
                "success_rate", "gate_pass_rate", "evidence_compliance_rate",
                "format_compliance_rate", "dimension_rates")}
        row["repetitions"] = []
        for repetition in task.get("repetitions", []):
            row["repetitions"].append({
                key: repetition.get(key) for key in
                ("task_id", "score", "gate_passed", "task_passed", "correctness_rate",
                 "evidence_compliance_rate", "format_compliance_rate",
                 "dimension_rates")
            })
        tasks.append(row)
    return {
        "split": value.get("split"),
        "score_role": value.get("score_role"),
        "weights": value.get("weights"),
        "repetition_count": value.get("repetition_count"),
        "review_pending_count": value.get("review_pending_count"),
        "score_status": value.get("score_status"),
        "mean_score": value.get("mean_score"),
        "score_sd_across_repetitions": value.get("score_sd_across_repetitions"),
        "task_success_rate": value.get("task_success_rate"),
        "evidence_compliance_rate": value.get("evidence_compliance_rate"),
        "format_compliance_rate": value.get("format_compliance_rate"),
        "tasks": tasks,
    }


def replay(report_zip, split, expected):
    with tempfile.TemporaryDirectory(prefix=f"v22-{split}-replay-") as temporary:
        root = Path(temporary) / split
        extract(report_zip, root)
        values = []
        for repetition in sorted(root.glob("rep-*")):
            reports = repetition / "reports"
            path = reports if (reports / "run_manifest.json").is_file() else repetition
            values.append(evaluate(split, path))
        value = aggregate(values)
    if score_signature(value) != score_signature(expected):
        raise ValueError(f"{split} evaluator replay differs from archived score")
    return value


def validate_inputs(official, supplement):
    official = Path(official).resolve()
    supplement = Path(supplement).resolve()
    official_checkpoint = verify_archive_checkpoint(official)
    supplement_checkpoint = verify_archive_checkpoint(supplement)
    train_zip = checked_file(official, "train-reports.zip")
    selection_zip = checked_file(supplement, "selection-complete-reports.zip")
    official_freeze_path = checked_file(official, "generation/freeze.json")
    supplement_freeze_path = checked_file(supplement, "generation/freeze.json")
    official_freeze = read_json(official_freeze_path)
    supplement_freeze = read_json(supplement_freeze_path)
    old_identity = supplement_freeze["identity"]
    current_identity = identity(bundle_mode=True)
    differences = {key for key in set(old_identity) | set(current_identity)
                   if old_identity.get(key) != current_identity.get(key)}
    if differences != {"fixed_hash"}:
        raise ValueError(f"policy migration must change only fixed_hash, got {sorted(differences)}")
    if official_freeze["identity"]["v0_skill_hash"] != current_identity["v0_skill_hash"]:
        raise ValueError("measured Skill bundle differs from the current baseline")
    if official_freeze["identity"]["taskset_hash"] != current_identity["taskset_hash"] or \
            official_freeze["identity"]["inputs_hash"] != current_identity["inputs_hash"]:
        raise ValueError("measured task set or inputs changed")
    if official_freeze["identity"]["model_steps"] != current_identity["model_steps"] or \
            official_freeze["identity"]["timeout_seconds"] != current_identity["timeout_seconds"] or \
            official_freeze["identity"]["resource_limits"] != current_identity["resource_limits"]:
        raise ValueError("measured budget or resource limits changed")
    train_score = read_json(checked_file(official, "generation/train-score.json"))
    selection_score = read_json(checked_file(supplement, "generation/selection-score.json"))
    train_replay = replay(train_zip, "train", train_score)
    selection_replay = replay(selection_zip, "selection", selection_score)
    if train_replay["mean_score"] != train_score["mean_score"] or \
            selection_replay["mean_score"] != selection_score["mean_score"]:
        raise ValueError("replayed mean differs from archived baseline")
    for split, value in (("train", train_score), ("selection", selection_score)):
        expected_ids = [task["id"] for task in load_tasks(split)]
        if [task["task_id"] for task in value["tasks"]] != expected_ids:
            raise ValueError(f"{split} baseline task set/order mismatch")
        if value["repetition_count"] != 3 or value["review_pending_count"] != 0:
            raise ValueError(f"{split} baseline is incomplete or has pending reviews")
    equivalence_path = checked_file(supplement, "generation/provider-equivalence.json")
    equivalence = read_json(equivalence_path)
    if equivalence.get("kind") != "user_confirmed_provider_equivalence":
        raise ValueError("provider equivalence record is missing")
    return {
        "official": official,
        "supplement": supplement,
        "official_checkpoint_sha256": file_hash(official / "checkpoint.json"),
        "supplement_checkpoint_sha256": file_hash(supplement / "checkpoint.json"),
        "official_checkpoint": official_checkpoint,
        "supplement_checkpoint": supplement_checkpoint,
        "train_zip": train_zip,
        "selection_zip": selection_zip,
        "train_score_path": checked_file(official, "generation/train-score.json"),
        "selection_score_path": checked_file(supplement, "generation/selection-score.json"),
        "train_score": train_score,
        "selection_score": selection_score,
        "provider_equivalence": equivalence,
        "provider_equivalence_path": equivalence_path,
        "official_freeze": official_freeze,
        "supplement_freeze": supplement_freeze,
        "current_identity": current_identity,
        "identity_differences": sorted(differences),
    }


def freeze(output, official, supplement, history):
    output = Path(output).resolve()
    history = Path(history).resolve()
    if output.exists():
        raise FileExistsError("preserve the existing baseline freeze")
    if not output.is_relative_to(history) or history == output:
        raise ValueError("migration freeze must be stored inside the local evaluation history")
    if history == ROOT or history.is_relative_to(ROOT) or ROOT.is_relative_to(history):
        raise ValueError("RSI measurement history must remain outside the public project")
    if git(history, "remote"):
        raise ValueError("RSI measurement history must remain local-only")
    if git(history, "status", "--porcelain"):
        raise ValueError("commit all prior evaluation-history work before freezing")
    if git(ROOT, "status", "--porcelain"):
        raise ValueError("commit reviewed project source before freezing")
    validated = validate_inputs(official, supplement)
    temporary = output.with_name("." + output.name + ".tmp")
    if temporary.exists():
        raise FileExistsError("preserve incomplete migration staging directory")
    temporary.mkdir(parents=True)
    try:
        bundle = bundles.create(temporary / "bundle")
        if bundles.digest(bundle) != validated["current_identity"]["v0_skill_hash"]:
            raise ValueError("current bundle hash differs from measured baseline")
        shutil.copyfile(validated["train_zip"], temporary / "train-reports.zip")
        shutil.copyfile(validated["selection_zip"], temporary / "selection-reports.zip")
        checkpoint(temporary / "train-score.json", validated["train_score"])
        checkpoint(temporary / "selection-score.json", validated["selection_score"])
        checkpoint(temporary / "provider-equivalence.json", validated["provider_equivalence"])
        package(temporary / "source.zip")
        migration = {
            "kind": "rsi_policy_baseline_migration_v1",
            "created_at_utc": utc(),
            "new_model_calls": 0,
            "source_commit": git(ROOT, "rev-parse", "HEAD"),
            "source_archive_sha256": file_hash(temporary / "source.zip"),
            "old_identity": validated["supplement_freeze"]["identity"],
            "new_identity": validated["current_identity"],
            "identity_differences": validated["identity_differences"],
            "train_measurement": {
                "archive": str(validated["official"]),
                "checkpoint_sha256": validated["official_checkpoint_sha256"],
                "freeze_sha256": file_hash(validated["official"] / "generation/freeze.json"),
                "reports_sha256": file_hash(validated["train_zip"]),
                "model": validated["official_freeze"]["identity"]["model"],
                "mean_score": validated["train_score"]["mean_score"],
                "task_success_rate": validated["train_score"]["task_success_rate"],
                "repetition_count": validated["train_score"]["repetition_count"],
            },
            "selection_measurement": {
                "archive": str(validated["supplement"]),
                "checkpoint_sha256": validated["supplement_checkpoint_sha256"],
                "freeze_sha256": file_hash(validated["supplement"] / "generation/freeze.json"),
                "reports_sha256": file_hash(validated["selection_zip"]),
                "model": validated["supplement_freeze"]["identity"]["model"],
                "mean_score": validated["selection_score"]["mean_score"],
                "task_success_rate": validated["selection_score"]["task_success_rate"],
                "repetition_count": validated["selection_score"]["repetition_count"],
            },
            "model_equivalence": {
                "record": validated["provider_equivalence"],
                "record_sha256": file_hash(validated["provider_equivalence_path"]),
            },
            "checks": [
                "all source and supplement checkpoint hashes verified",
                "no model calls during migration",
                "current evaluator replay matches archived train and selection scores",
                "three repetitions and complete scientific reviews verified",
                "task set/order, inputs, budgets, harness and runtime unchanged",
                "measured Skill bundle equals current baseline bundle",
                "old-to-new identity differs only in fixed_hash",
            ],
            "limitations": [
                "the train measurements retain their archived execution identity",
                "the selection measurements retain their archived execution identity",
                "this is inherited measurement with zero new baseline sessions",
            ],
        }
        checkpoint(temporary / "migration.json", migration)
        freeze_record = {
            "kind": "frozen_pre_v0_protocol",
            "created_at_utc": utc(),
            "formal_baseline_executed": True,
            "git_commit": migration["source_commit"],
            "identity": validated["current_identity"],
            "source_archive_sha256": migration["source_archive_sha256"],
            "migration_sha256": file_hash(temporary / "migration.json"),
            "protocol": protocol(),
            "purpose": "inherited v2.2 policy-only baseline for v3 RSI",
        }
        checkpoint(temporary / "freeze.json", freeze_record)
        files = {path.relative_to(temporary).as_posix(): file_hash(path)
                 for path in temporary.rglob("*")
                 if path.is_file() and path.name != "checkpoint.json"}
        checkpoint(temporary / "checkpoint.json", {
            "kind": "rsi_policy_baseline_migration_archive_v1",
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
        "Freeze inherited v2.2 baseline under noise-aware RSI policy", "--only", "--", relative)
    commit = git(history, "log", "-1", "--format=%H", "--", relative)
    if not commit or git(history, "status", "--porcelain"):
        raise RuntimeError("baseline migration archive was not committed cleanly")
    return {"output": str(output), "git_commit": commit, "freeze_sha256": file_hash(output / "freeze.json")}


def initialize_run(run, freeze_path, migration_archive, history):
    run = Path(run).resolve()
    freeze_path = Path(freeze_path).resolve()
    migration_archive = Path(migration_archive).resolve()
    history = Path(history).resolve()
    if run.exists():
        raise FileExistsError("preserve the existing v3 campaign")
    frozen = verify(freeze_path)
    if frozen.get("formal_baseline_executed") is not True:
        raise ValueError("require an inherited measured baseline")
    migration = read_json(migration_archive / "migration.json")
    if migration.get("kind") != "rsi_policy_baseline_migration_v1" or migration.get("new_model_calls") != 0:
        raise ValueError("invalid migration provenance")
    if frozen["migration_sha256"] != file_hash(migration_archive / "migration.json"):
        raise ValueError("freeze migration digest mismatch")
    train = read_json(migration_archive / "train-score.json")
    selection = read_json(migration_archive / "selection-score.json")
    if train["mean_score"] != migration["train_measurement"]["mean_score"] or \
            selection["mean_score"] != migration["selection_measurement"]["mean_score"]:
        raise ValueError("migrated scores changed")
    initial = bundles.create(_temporary_bundle_path(run))
    try:
        if bundles.digest(initial) != frozen["identity"]["v0_skill_hash"]:
            raise ValueError("campaign bundle differs from frozen baseline")
        run.mkdir(parents=True)
        v0 = run / "v0"
        v0.mkdir()
        shutil.copytree(initial, v0 / "bundle")
        shutil.copyfile(freeze_path, run / "freeze-record.json")
        checkpoint(v0 / "train.json", train)
        checkpoint(v0 / "selection.json", selection)
        checkpoint(v0 / "baseline-origin.json", migration)
        shutil.copyfile(migration_archive / "train-reports.zip", v0 / "train-reports.zip")
        shutil.copyfile(migration_archive / "selection-reports.zip", v0 / "selection-reports.zip")
        state = {
            "kind": "three-skill-rsi-campaign-v1",
            "round": 0,
            "version_offset": 2,
            "incumbent": "v0",
            "incumbent_hash": frozen["identity"]["v0_skill_hash"],
            "model": frozen["identity"]["model"],
            "harness": frozen["identity"]["harness"],
            "fixed_hash": frozen["identity"]["fixed_hash"],
            "freeze_sha256": digest(run / "freeze-record.json"),
            "baseline_origin": "explicitly inherited v2.2; policy-only migration; zero new baseline calls",
            "history_directory": str(history),
            "migration_archive": str(migration_archive),
        }
        checkpoint(run / "state.json", state)
        baseline = {
            "round": 2,
            "version": "v2.2",
            "target": "delivery-fix",
            "train": train["mean_score"],
            "selection": selection["mean_score"],
            "accepted": True,
            "gain": migration["selection_measurement"]["mean_score"] -
                    read_json(history / "v0/generation/selection.json")["mean_score"],
            "note": "inherited baseline under policy-only migration",
        }
        checkpoint(run / "history.json", [baseline])
        stream = io.StringIO(newline="")
        writer = csv.DictWriter(stream, fieldnames=["round", "version", "target", "train", "selection", "accepted", "gain"])
        writer.writeheader()
        writer.writerow({key: baseline[key] for key in writer.fieldnames})
        (run / "curve.csv").write_text(stream.getvalue(), encoding="utf-8")
        from .versioning import checkpoint as archive_version
        commit = archive_version(run, "v0", v0 / "train-reports.zip", v0 / "selection-reports.zip", history)
        checkpoint(v0 / "git-record.json", {
            "commit": commit,
            "journal": "local-only outside public project",
            "migration_freeze": str(freeze_path),
        })
    finally:
        shutil.rmtree(initial.parent, ignore_errors=True)
    return {"run": str(run), "incumbent": "v2.2", "next_candidate": "v3",
            "model_calls": 0, "freeze_sha256": digest(run / "freeze-record.json"),
            "git_archive": commit}


def _temporary_bundle_path(run):
    parent = run.parent.resolve()
    parent.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=f".{run.name}-bundle-", dir=parent)) / "bundle"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("freeze", "init"))
    parser.add_argument("--official", required=True)
    parser.add_argument("--supplement", required=True)
    parser.add_argument("--out", help="new external baseline migration archive")
    parser.add_argument("--history", required=True)
    parser.add_argument("--run", help="new RSI campaign directory")
    args = parser.parse_args()
    if args.action == "freeze":
        if not args.out:
            parser.error("freeze requires --out")
        result = freeze(args.out, args.official, args.supplement, args.history)
    else:
        if not args.run or not args.out:
            parser.error("init requires --run and --out")
        result = initialize_run(args.run, Path(args.out) / "freeze.json", args.out, args.history)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
