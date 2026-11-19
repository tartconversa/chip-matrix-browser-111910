import json
from pathlib import Path
from tempfile import TemporaryDirectory
from playwright.sync_api import expect
from tests.support import SourceTest

class MatrixTest(SourceTest):
    def test_visible_controls_revision_and_download(self):
        p=self.browser();p.goto(self.base);expect(p.locator('#loading')).to_have_text('已完成')
        p.select_option('#card','c2');expect(p.locator('#driver')).to_have_value('stable');expect(p.locator('#identity')).to_contain_text('c2|stable')
        p.select_option('#card','c1');expect(p.locator('#driver')).to_have_value('d10');expect(p.locator('#loading')).to_have_text('已完成')
        self.control('correct',card='c1');p.reload();expect(p.locator('#loading')).to_have_text('已完成');p.click('#detail')
        expect(p.locator('#revision')).to_have_text('修订 2');expect(p.locator('#operator')).to_have_text('失败');expect(p.locator('#build')).to_contain_text('10.12.3')
        with p.expect_download() as got:p.click('#original')
        with TemporaryDirectory() as d:
            path=Path(d)/'result';got.value.save_as(path);self.assertEqual(json.loads(path.read_bytes())['revision'],2)
        self.control('framework');p.goto(self.base);expect(p.locator('#loading')).to_have_text('已完成');p.select_option('#framework','next');expect(p.locator('#status')).to_have_text('尚未测')
