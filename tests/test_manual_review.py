import hashlib
from pathlib import Path
import tempfile
import unittest
from evaluation.evaluate import manual_review


class ManualReviewTests(unittest.TestCase):
    def test_review_is_required_and_bound_to_report(self):
        task={'id':'demo','expected':{'manual_checks':[{'id':'physical-model'}]}}
        with tempfile.TemporaryDirectory() as tmp:
            directory=Path(tmp);report=directory/'report.json';report.write_text('{}')
            self.assertFalse(manual_review(task,directory,None)[0]['passed'])
            review={'task_id':'demo','reviewer':'operator','report_sha256':hashlib.sha256(report.read_bytes()).hexdigest(),
                    'checks':{'physical-model':{'passed':True,'reason':'Read derivation and checked assumptions.'}}}
            self.assertTrue(manual_review(task,directory,review)[0]['passed'])
            report.write_text('{"changed":true}')
            self.assertFalse(manual_review(task,directory,review)[0]['passed'])

    def test_boolean_without_explanation_is_not_a_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory=Path(tmp);(directory/'report.json').write_text('{}')
            review={'task_id':'demo','reviewer':'operator','report_sha256':hashlib.sha256(b'{}').hexdigest(),
                    'checks':{'model':{'passed':True}}}
            self.assertFalse(manual_review({'id':'demo','expected':{'manual_checks':[{'id':'model'}]}},directory,review)[0]['passed'])
