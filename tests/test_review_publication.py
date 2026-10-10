"""Integration boundaries discovered by the independent v1.3 forward review."""
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE / 'modules/testcase-builder/scripts'))
from qa_cases import case_matrix, validate_cases
from review_context import review_fingerprints
from prepare_review import create_template
import update_review


class ReviewPublicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name)/'project'
        shutil.copytree(PACKAGE/'example', self.project)
        self.module = self.project/'output/商品管理'

    def tearDown(self):
        self.temp.cleanup()

    def read(self, name):
        return json.loads((self.module/name).read_text())

    def write(self, name, data):
        (self.module/name).write_text(json.dumps(data, ensure_ascii=False))

    def author_current_review(self):
        spec = self.read('REQ清单.json')
        cases = validate_cases(self.read('用例数据.json'), spec, module_dir=self.module)
        review = self.read('评审数据.json')
        review.update(reviewed_source=review_fingerprints(spec, cases, self.module),
                      reviewer='Author of this current regression fixture')
        self.write('评审数据.json', review)
        return cases

    def args(self):
        return ['update_review.py','--module-dir',str(self.module),
                '--review',str(self.module/'评审数据.json')]

    def test_prepare_locates_real_structured_framework(self):
        framework = self.module/'framework'; framework.mkdir()
        function = framework/'api.py'
        function.write_text('class API:\n    def submit(self):\n        pass\n')
        manifest = {'schema_version':'1.0','framework_root':'framework','reviewer':'Fixture author',
                    'checked_at':'2026-10-08','capabilities':[{'id':'SUBMIT','kind':'function',
                    'file':'api.py','symbol':'API.submit','notes':'Defined fixture method, never executed.',
                    'sha256':hashlib.sha256(function.read_bytes()).hexdigest()}]}
        self.write('框架能力清单.json',manifest)
        data = self.read('用例数据.json')
        for case in data['cases']:
            case.update(automation_evidence=['SUBMIT'], 自动化建议='仅api自动化可用',
                        自动化建议明细='理由：本测试仅核对真实Python接口入口。\n已有函数/资源：API.submit定义，未执行。')
        self.write('用例数据.json',data)
        path = create_template(self.module)
        form = json.loads(path.read_text())
        self.assertTrue(form['draft'])
        self.assertEqual(form['reviews'],[])
        self.assertIn('framework_evidence_sha256',form['reviewed_source'])

    def test_report_destination_cannot_overwrite_actual_requirement_source(self):
        evidence = self.module/'评审报告.md'
        original = (self.project/'input/新增商品.md').read_bytes()
        evidence.write_bytes(original)
        spec = self.read('REQ清单.json')
        for req in spec['requirements']:
            req['source']['file']='商品管理/评审报告.md'
        self.write('REQ清单.json',spec)
        self.author_current_review()
        with patch.object(sys,'argv',self.args()), patch.object(update_review,'run_xlsx') as export:
            self.assertEqual(update_review.main(),1)
            export.assert_not_called()
        self.assertEqual(evidence.read_bytes(),original)

    def test_source_change_during_snapshot_read_is_rejected_before_export(self):
        cases = self.author_current_review()
        source = self.module/'详细用例.xlsx'
        def raced_read(*args,**kwargs):
            source.write_bytes(b'changed during read')
            return {'name':'用例','rows':case_matrix(cases)}
        with patch.object(sys,'argv',self.args()), patch.object(update_review,'read_xlsx',side_effect=raced_read), \
             patch.object(update_review,'run_xlsx') as export:
            self.assertEqual(update_review.main(),1)
            export.assert_not_called()

    def test_preserved_workbook_records_feed_report_without_claiming_execution_pass(self):
        cases = self.author_current_review()
        snapshot = {'name':'用例','rows':case_matrix(cases)}
        snapshot['rows'][1][7]='Human-entered execution record; not proof of software pass.'
        def fake_export(payload,output,*,publication_guard=None):
            publication_guard()
            output.write_bytes(b'isolated export fixture')
        with patch.object(sys,'argv',self.args()), patch.object(update_review,'read_xlsx',return_value=snapshot), \
             patch.object(update_review,'run_xlsx',side_effect=fake_export):
            self.assertEqual(update_review.main(),0)
        report = self.read('执行准备数据.json')
        self.assertEqual(report['counts']['execution_records_present'],1)
        self.assertNotIn('execution_passed',report['counts'])
        self.assertEqual(report['counts']['automation_ready'],0)
        # The default coverage entry reconsumes this published result. Preserve
        # reviewer and the original binding instead of producing an unusable result.
        result = self.read('评审结果.json')
        spec = self.read('REQ清单.json')
        update_review.validate_review(result,cases,spec,self.module)
        self.assertEqual(result['reviewer'],'Author of this current regression fixture')


if __name__=='__main__':
    unittest.main()
