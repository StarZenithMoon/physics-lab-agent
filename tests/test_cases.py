from pathlib import Path
import tempfile
import unittest
from labcore.io import read_json, write_json
from labcore.project import ROOT
from evaluation.oracles import validate_public
from rsi.runner import prepare_workspace, run_split, input_digest
from rsi.common import TARGET


class CaseBankTests(unittest.TestCase):
    def test_public_references_against_adapters_and_independent_math(self):
        result=validate_public()
        self.assertEqual(len(result['tasks']),24)
        self.assertTrue(result['all_passed'],[t['task_id'] for t in result['tasks'] if not t['passed']])

    def test_related_public_variants_do_not_cross_split(self):
        groups={}
        tasks=read_json(ROOT/'evaluation/public-cases.json')['tasks']
        for task in tasks:groups.setdefault(task['group'],set()).add(task['split'])
        self.assertTrue(all(len(splits)==1 for splits in groups.values()))

    def test_external_private_workspace_copies_only_curated_input(self):
        with tempfile.TemporaryDirectory() as folder:
            private=Path(folder)/'private';(private/'cases/example/input').mkdir(parents=True)
            write_json(private/'cases/example/input/data.json',{'reading':2})
            write_json(private/'cases/example/judge/answer.json',{'secret':99})
            task={'id':'example','public':{'data_directory':'cases/example/input','instruction':'compute'},'expected':{'secret':99}}
            work=Path(folder)/'work';work.mkdir()
            before=input_digest([task],private)
            prepare_workspace(work,task,ROOT/TARGET,private)
            self.assertTrue((work/'cases/example/input/data.json').is_file())
            self.assertFalse((work/'cases/example/judge').exists())
            self.assertNotIn('secret',(work/'task.json').read_text(encoding='utf-8'))
            write_json(private/'cases/example/input/data.json',{'reading':3})
            self.assertNotEqual(before,input_digest([task],private))

    def test_private_report_location_cannot_enter_public_project(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'tasks.json'
            write_json(path,{'visibility':'private','tasks':[{'id':'private','split':'test','public':{'instruction':'test'},'expected':{}}]})
            with self.assertRaisesRegex(ValueError,'outside the public'):
                run_split('test',ROOT/TARGET,ROOT/'results/forbidden-private-output',task_path=path)


if __name__=='__main__':unittest.main()
