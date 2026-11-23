from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from labcore.io import read_json
from rsi.checkpoint import checkpoint
from rsi.common import config,validate_config


class CheckpointTests(unittest.TestCase):
    def test_failed_replace_preserves_previous_complete_record(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'manifest.json'
            checkpoint(path,{'tasks':['completed']})
            with patch('rsi.checkpoint.os.replace',side_effect=PermissionError('temporary external reader')):
                with self.assertRaises(PermissionError):checkpoint(path,{'tasks':['completed','next']})
            self.assertEqual(read_json(path),{'tasks':['completed']})
            self.assertEqual(list(Path(folder).iterdir()),[path])
            checkpoint(path,{'tasks':['completed','next']})
            self.assertEqual(read_json(path)['tasks'],['completed','next'])

    def test_resource_limits_cannot_be_disabled_by_configuration(self):
        cfg=config()
        for override in ({'memory_limit_mb':0},{'memory_limit_mb':65536},{'cpu_limit_percent':100},
                         {'max_processes':0},{'runtime_free_floor_mb':999999}):
            with self.subTest(override=override):
                with self.assertRaises(ValueError):
                    validate_config({**cfg,'resources':{**cfg['resources'],**override}},cfg['model_steps'])


if __name__=='__main__':unittest.main()
