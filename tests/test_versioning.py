from pathlib import Path
import shutil
import tempfile
import unittest
from zipfile import ZipFile
from labcore.io import write_json
from labcore.project import ROOT
from rsi.versioning import checkpoint,git


@unittest.skipUnless(shutil.which('git'),'Git is required for actual RSI archives')
class GitJournalTests(unittest.TestCase):
    def test_snapshot_has_reports_and_local_git_commit(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);run=root/'fixture-experiment';(run/'v0').mkdir(parents=True)
            (run/'v0/SKILL.md').write_text('fixture, not a real model result',encoding='utf-8')
            for split in ['train','selection']:write_json(root/split/'run_manifest.json',{'mode':'unit-test-fixture'})
            history=root/'journal'
            commit=checkpoint(run,'v0',root/'train',root/'selection',history)
            self.assertEqual(len(commit),40)
            with ZipFile(history/'fixture-experiment/v0/train-reports.zip') as archive:
                self.assertIn('run_manifest.json',archive.namelist())
            self.assertTrue((history/'fixture-experiment/v0/generation/SKILL.md').is_file())
            self.assertFalse(git(history,'remote'))
            with self.assertRaises(FileExistsError):checkpoint(run,'v0',root/'train',root/'selection',history)

    def test_archive_cannot_enter_public_repo(self):
        with self.assertRaisesRegex(ValueError,'outside'):
            checkpoint(ROOT/'rsi/runs/fixture','v0',ROOT,ROOT,ROOT/'results/private-journal')


if __name__=='__main__':unittest.main()
