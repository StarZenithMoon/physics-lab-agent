import json
from pathlib import Path
import tempfile
import unittest
from labcore.io import read_json, write_json
from evaluation.evaluate import score_task, assertion, evaluate
from rsi.outer_loop import decision, checked_evaluation
from rsi.propose import make_request
from rsi.runner import run_split, prepare_workspace
from rsi.common import ROOT, TARGET, validate_candidate, validate_config, config
from rsi.campaign import next_round


class EvaluationTests(unittest.TestCase):
    def test_correct_value_without_real_evidence_cannot_pass_gate(self):
        task = {"id": "example", "expected": {"checks": [{"path": "result.u", "target": .1, "atol": .001, "critical": True}],
                                               "evidence": ["calculation.json"]}}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            write_json(path/"report.json", {"result": {"u": .1}, "evidence": ["calculation.json"]})
            result = score_task(task, path)
        self.assertFalse(result["gate_passed"])
        self.assertEqual(result["score"], 60)

    def test_missing_report_and_nonnumeric_claim_fail(self):
        with tempfile.TemporaryDirectory() as folder:
            result = score_task({"id": "missing", "expected": {}}, folder)
        self.assertEqual(result["score"], 0)
        self.assertFalse(assertion({"u": float("inf")}, {"path": "u", "target": 1, "atol": 1}))
        self.assertFalse(assertion({"u": True}, {"path": "u", "target": 1, "atol": 1}))

    def test_evidence_path_traversal_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root/"report").mkdir()
            (root/"secret.csv").write_text("x\n1\n", encoding="utf-8")
            write_json(root/"report/report.json", {"status": "complete", "evidence": ["../secret.csv"]})
            task = {"id": "escape", "expected": {"checks": [{"path": "status", "equals": "complete", "critical": True}],
                                                "evidence": ["../secret.csv"]}}
            self.assertFalse(score_task(task, root/"report")["gate_passed"])

    def test_malformed_report_fails_safely(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            write_json(path/"report.json", [])
            task = {"id": "malformed", "expected": {"checks": [], "evidence": []}}
            result = score_task(task, path)
            self.assertEqual(result["score"], 0)
            self.assertFalse(result["gate_passed"])

    def test_success_and_evidence_are_reported_separately_from_diagnostic_score(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            checks = [{"path": "status", "equals": "needs_information", "critical": True}]
            tasks = [{"id": name, "split": "train", "expected": {"checks": checks, "evidence": ["intake.json"]}}
                     for name in ["correct", "wrong"]]
            write_json(root/"tasks.json", {"tasks": tasks})
            for name, status in [("correct", "needs_information"), ("wrong", "complete")]:
                path = root/name
                write_json(path/"report.json", {"status": status, "evidence": ["intake.json"]})
                write_json(path/"intake.json", {"missing": ["unit"]})
                (path/"report.md").write_text("## 数据与任务\n\n## 测量模型与假设\n\n## 结果与局限\n\n## 复现与来源\n", encoding="utf-8")
            result = evaluate("train", root, root/"tasks.json")
            self.assertEqual(result["task_success_rate"], .5)
            self.assertEqual(result["evidence_compliance_rate"], 1)
            self.assertNotEqual(result["mean_score"], 100*result["task_success_rate"])
            self.assertEqual(result["score_role"], "development_diagnostic")


def scores(a, b):
    return {"mean_score": (a+b)/2, "tasks": [{"task_id": "a", "score": a, "gate_passed": True},
                                              {"task_id": "b", "score": b, "gate_passed": True}]}


class RSITests(unittest.TestCase):
    def test_rounds_are_configurable_and_steps_must_match_runtime(self):
        cfg = config()
        for rounds in (1, 2, 5):
            self.assertEqual(validate_config({**cfg, "rounds": rounds}, cfg["model_steps"])["rounds"], rounds)
        with self.assertRaises(ValueError):
            validate_config({**cfg, "rounds": 0}, cfg["model_steps"])
        with self.assertRaisesRegex(ValueError, "match"):
            validate_config({**cfg, "model_steps": cfg["model_steps"]+1}, cfg["model_steps"])
        with self.assertRaises(ValueError):
            validate_config({**cfg, "min_score_gain": float("nan")}, cfg["model_steps"])
        with self.assertRaises(ValueError):
            validate_config({**cfg, "max_task_pass_rate_regression": 1.1}, cfg["model_steps"])
        with self.assertRaises(ValueError):
            validate_config({**cfg, "overall_observation_regression_allowance": 1.5},
                            cfg["model_steps"])

    def test_accept_any_positive_gain_reject_no_gain_or_substantial_regression(self):
        self.assertTrue(decision(scores(60, 70), scores(65, 75))["accepted"])
        self.assertTrue(decision(scores(60, 70), scores(60.1, 70.1))["accepted"])
        self.assertEqual(decision(scores(60, 70), scores(80, 64))["reason"], "task_regression")
        self.assertEqual(decision(scores(60, 70), scores(60, 70))["reason"], "insufficient_gain")
        self.assertTrue(decision(scores(60, 70), scores(80, 67))["accepted"])
        changed_gate = scores(80, 90)
        changed_gate["tasks"][1]["gate_passed"] = False
        self.assertFalse(decision(scores(60, 70), changed_gate)["accepted"])

    def test_inherited_baseline_keeps_local_rounds_but_names_next_candidate_v3(self):
        self.assertEqual(next_round({"round": 0, "version_offset": 2}),
                         ("v3", "measurement-uncertainty"))

    def test_prepared_runs_do_not_become_rsi_measurements(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)/"prepared"
            manifest = run_split("train", ROOT/TARGET, output)
            self.assertFalse(manifest["executed"])
            self.assertEqual(manifest["mode"], "prepared_only")
            for task in manifest["tasks"]:
                request = read_json(output/"_runner"/task["task_id"]/"request.json")
                self.assertNotIn("expected", request)
            with self.assertRaisesRegex(ValueError, "cannot be used"):
                checked_evaluation("train", output, manifest["skill_hash"])

    def test_workspace_excludes_answers_and_keeps_candidate(self):
        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder)
            prepare_workspace(work, {"id": "example", "public": {"instruction": "test"}, "expected": {"secret": 9}}, ROOT/TARGET)
            self.assertFalse((work/"evaluation").exists())
            self.assertNotIn("secret", (work/"task.json").read_text(encoding="utf-8"))
            self.assertEqual((work/TARGET).read_bytes(), (ROOT/TARGET).read_bytes())

    def test_proposal_contains_training_feedback_only(self):
        with tempfile.TemporaryDirectory() as folder:
            run = Path(folder)
            (run/"SKILL.md").write_text((ROOT/TARGET).read_text(encoding="utf-8"), encoding="utf-8")
            write_json(run/"state.json", {"incumbent_file": "SKILL.md", "training_feedback": "train.json"})
            write_json(run/"train.json", {"message": "TRAIN_FEEDBACK"})
            write_json(run/"selection.json", {"message": "PRIVATE_SELECTION_SENTINEL"})
            make_request(run, run/"request.md")
            text = (run/"request.md").read_text(encoding="utf-8")
            self.assertIn("TRAIN_FEEDBACK", text)
            self.assertNotIn("PRIVATE_SELECTION_SENTINEL", text)

    def test_candidate_cannot_change_name_or_inject_yaml_fields(self):
        validate_candidate(ROOT/TARGET)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/"SKILL.md"
            path.write_text("---\nname: other\ndescription: test\n---\nbody", encoding="utf-8")
            with self.assertRaises(ValueError):
                validate_candidate(path)


if __name__ == "__main__":
    unittest.main()
