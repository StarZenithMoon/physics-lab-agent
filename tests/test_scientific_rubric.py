import tempfile
import unittest
from pathlib import Path
from evaluation.evaluate import score_task, evaluate
from evaluation.rubric import specification, review_template, VERSION
from labcore.io import read_json, write_json
from labcore.project import ROOT
from rsi.outer_loop import decision


class ScientificRubricTests(unittest.TestCase):
    def fixture(self, root):
        task = {"id": "case", "split": "train", "capabilities": ["clarification"],
                "public": {"instruction": "no unit"},
                "expected": {"checks": [{"path": "status", "equals": "needs_information", "critical": True},
                                         {"path": "result", "equals": None, "critical": True}], "evidence": ["intake.json"]}}
        task["expected"]["rubric"] = specification(task)
        path = root / "case"
        write_json(path / "report.json", {"status": "needs_information", "result": None, "evidence": ["intake.json"]})
        write_json(path / "intake.json", {"missing": ["unit"]})
        (path / "report.md").write_text("单位缺失，需确认后计算。", encoding="utf-8")
        write_json(root / "_runner/case/request.json", {"task": "public request"})
        review = review_template(task, path)
        review["reviewer"] = "test reviewer"
        for r in review["rubric"].values():
            r.update(value=1, reason="Verified required input boundary against source", evidence="intake.json:missing; report.md")
        return task, path, review

    def test_pending_cannot_pass_even_when_automatic_results_are_correct(self):
        with tempfile.TemporaryDirectory() as tmp:
            task, path, _ = self.fixture(Path(tmp))
            result = score_task(task, path)
            self.assertEqual(result["score_status"], "pending_review")
            self.assertEqual(result["dimension_rates"]["outcome"], 1)
            self.assertFalse(result["task_passed"])

    def test_correct_clarification_needs_no_fake_numbers_or_heading_bonus(self):
        with tempfile.TemporaryDirectory() as tmp:
            task, path, review = self.fixture(Path(tmp))
            result = score_task(task, path, review=review)
            self.assertTrue(result["task_passed"])
            self.assertEqual(result["score"], 100)
            self.assertEqual(result["format_compliance_rate"], 0)

    def test_scientific_error_cannot_be_offset_by_correct_numbers(self):
        with tempfile.TemporaryDirectory() as tmp:
            task, path, review = self.fixture(Path(tmp))
            review["rubric"]["method"]["value"] = .5
            result = score_task(task, path, review=review)
            self.assertTrue(result["review_complete"])
            self.assertEqual(result["score"], 90)
            self.assertFalse(result["gate_passed"])

    def test_markdown_evidence_and_trace_changes_invalidate_review(self):
        for name in ("report.md", "intake.json", "../_runner/case/request.json"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                task, path, review = self.fixture(Path(tmp))
                artifact = path / name
                artifact.write_text(artifact.read_text(encoding="utf-8") + " ", encoding="utf-8")
                result = score_task(task, path, review=review)
                self.assertFalse(result["review_complete"])

    def test_task_change_invalidates_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            task, path, review = self.fixture(Path(tmp))
            task["public"]["instruction"] = "changed"
            self.assertFalse(score_task(task, path, review=review)["review_complete"])

    def test_boolean_or_missing_reason_or_evidence_is_not_grade(self):
        for changes in ({"value": True}, {"reason": " "}, {"evidence": ""}):
            with self.subTest(changes=changes), tempfile.TemporaryDirectory() as tmp:
                task, path, review = self.fixture(Path(tmp))
                review["rubric"]["method"].update(changes)
                self.assertFalse(score_task(task, path, review=review)["review_complete"])

    def test_pending_batch_blocked_from_selection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task, _, _ = self.fixture(root)
            write_json(root / "tasks.json", {"tasks": [task]})
            result = evaluate("train", root, root / "tasks.json")
            self.assertEqual(result["review_pending_count"], 1)
            with self.assertRaisesRegex(ValueError, "reviews"):
                decision(result, result)

    def test_broken_rubric_is_not_charged_as_model_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            task, path, review = self.fixture(Path(tmp))
            task['expected']['rubric']['version'] = 'invalid-rubric'
            with self.assertRaisesRegex(ValueError, 'rubric version'):
                score_task(task, path, review=review)

    def test_public_tasks_have_persistent_task_specific_rubric(self):
        tasks = read_json(ROOT / "evaluation/public-cases.json")["tasks"]
        self.assertEqual(len(tasks), 24)
        for task in tasks:
            self.assertEqual(task["expected"]["rubric"], specification(task))
            self.assertEqual(task["expected"]["rubric"]["version"], VERSION)
        from tools.import_public_cases import task as make_task
        rebuilt = make_task("demo", "train", "test", [], [], {}, ["fit"])
        self.assertEqual(rebuilt["expected"]["rubric"]["version"], VERSION)

    def test_success_regression_blocks_even_if_partial_score_rises(self):
        baseline = {"mean_score": 80, "tasks": [{"task_id": "a", "score": 80, "gate_passed": False, "task_passed": True}]}
        candidate = {"mean_score": 90, "tasks": [{"task_id": "a", "score": 90, "gate_passed": False, "task_passed": False}]}
        self.assertFalse(decision(baseline, candidate)["accepted"])


if __name__ == "__main__":
    unittest.main()
