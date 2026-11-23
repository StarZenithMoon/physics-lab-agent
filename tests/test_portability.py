import json
from pathlib import Path
import tempfile
import unittest
from zipfile import ZipFile
from rsi.common import ROOT, config
from rsi.runner import prepare_workspace
from tools.package_source import package


class PortabilityTests(unittest.TestCase):
    def test_local_model_override_and_environment_precedence(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root/"rsi").mkdir()
            (root/"evaluation").mkdir()
            (root/".opencode/agents").mkdir(parents=True)
            (root/"rsi/config.json").write_bytes((ROOT/"rsi/config.json").read_bytes())
            declared_tasks=json.loads((ROOT/"rsi/config.json").read_text(encoding="utf-8"))["tasks"]
            (root/declared_tasks).write_text("{}", encoding="utf-8")
            (root/".opencode/agents/lab-assistant.md").write_bytes((ROOT/".opencode/agents/lab-assistant.md").read_bytes())
            (root/"rsi/config.local.json").write_text(json.dumps({"model": "provider/local"}), encoding="utf-8")
            self.assertEqual(config(root, {})["model"], "provider/local")
            self.assertEqual(config(root, {"LAB_MODEL": "provider/environment"})["model"], "provider/environment")
            (root/"rsi/config.local.json").write_text(json.dumps({"unknown": True}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "declared"):
                config(root, {})

    def test_source_export_excludes_local_data_runs_and_credentials(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name in ["README.md", ".env.example", ".env", ".env.personal", "labcore/__init__.py",
                         ".agents/skills/example/SKILL.md", ".github/workflows/check.yml",
                         "rsi/config.json", "rsi/config.local.json", "rsi/runs/private.json",
                         "data/local/private.csv", "data/demo-pendulum/raw.csv", "results/report.md",
                         ".venv/secret.txt", "labcore/__pycache__/private.py"]:
                path = root/name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("fixture", encoding="utf-8")
            output = root/"dist/source.zip"
            manifest = package(output, root)
            self.assertEqual(set(manifest["files"]), {"README.md", ".env.example", "labcore/__init__.py",
                ".agents/skills/example/SKILL.md", ".github/workflows/check.yml", "rsi/config.json", "data/demo-pendulum/raw.csv"})
            with ZipFile(output) as archive:
                self.assertIn("physics-lab-agent/SOURCE-MANIFEST.json", archive.namelist())
            with self.assertRaises(FileExistsError):
                package(output, root)

    def test_task_data_path_cannot_escape_project(self):
        for path in ("../data", "data/../../outside", str((ROOT/"data/demo-pendulum").resolve())):
            with self.subTest(path=path), tempfile.TemporaryDirectory() as folder:
                with self.assertRaisesRegex(ValueError, "project-relative"):
                    prepare_workspace(Path(folder), {"id": "example", "public": {"data_directory": path}},
                                      ROOT/".agents/skills/measurement-uncertainty/SKILL.md")


if __name__ == "__main__":
    unittest.main()
