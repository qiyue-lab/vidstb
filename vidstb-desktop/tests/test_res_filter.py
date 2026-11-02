"""资源库分组筛选的回归测试（直接跑前端函数）。

曾经的两个现象同源：点分组仍看到全部资源、已分组的资源又出现在「未分组」里。
根因是服务端 SSE 每 15 秒推一份「不带筛选条件」的全量 resources，而前端把
「已筛选的结果」也存进 state.resources，推送一到就被冲回全量。

修复后 state.resources 恒为全量，筛选一律由 filteredResItems() 在本地派生。
这里把该函数原样从 app.js 抽出来交给 node 执行：既校验筛选规则与后端一致，
也确认「数据被重新赋值成全量」不会让当前分组失效。

第二行改为「当前分组的网盘」后，另校验 resProviderCounts() 只统计当前分组、
syncResProviderFilter() 在切换分组后会清掉该分组不存在的网盘选择。

第三行按标题名分：校验 resTitleCounts() 只统计当前分组（并随已选网盘联动）、
同标题跨网盘聚合成一条、切换分组后清掉失效标题、第三行 HTML 渲染。
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP_JS = ROOT / "web" / "app.js"

# id / group 故意覆盖：两个同组、一个未分组、一个另一组
ITEMS = [
    {"id": "a", "title": "罗梅莉亚战记", "group": "g1", "provider": "quark",
     "status": "updating", "tags": ["日漫"], "note": "",
     "url": "https://pan.quark.cn/s/aaa", "update": None,
     "scan": {"files": [{"path": "/a/ep01.mp4"}]}},
    {"id": "b", "title": "仙逆", "group": "g1", "provider": "baidu",
     "status": "finished", "tags": ["国漫"], "note": "追完",
     "url": "https://pan.baidu.com/s/bbb", "update": {"new_count": 2},
     "scan": {"files": [{"path": "/仙逆/剧场版.mkv"}]}},
    {"id": "c", "title": "斗破苍穹", "group": "", "provider": "quark",
     "status": "updating", "tags": [], "note": "",
     "url": "https://pan.quark.cn/s/ccc", "update": None, "scan": None},
    {"id": "d", "title": "黑色五叶草", "group": "g2", "provider": "quark",
     "status": "dead", "tags": ["日漫", "热血"], "note": "失效",
     "url": "https://pan.quark.cn/s/ddd", "update": None,
     "scan": {"files": []}},
]


def _extract(source: str, name: str) -> str:
    # 顶层函数：函数体内的 } 都带缩进，所以 \n} 只会命中函数结尾
    match = re.search(r"\nfunction " + re.escape(name) + r"\(\) \{.*?\n\}",
                      source, re.S)
    if not match:
        raise AssertionError(f"app.js 里找不到 {name}()")
    return match.group(0)


@unittest.skipUnless(shutil.which("node"), "需要 node 才能执行前端函数")
class TestResFilter(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        source = APP_JS.read_text(encoding="utf-8")
        funcs = "\n".join(
            _extract(source, n) for n in
            ("filteredResItems", "resScopeItems", "resProviderCounts",
             "syncResProviderFilter", "renderResProviders",
             "resTitleScopeItems", "resTitleCounts", "syncResTitleFilter",
             "renderResTitles")
        )
        provider_const = re.search(r"const RES_PROVIDER_LIST = \[.*?\];",
                                   source, re.S).group(0)
        items = json.dumps(ITEMS, ensure_ascii=False)
        script = f"""
const RES_GROUP_NONE = '__none__';
{provider_const}
let state = {{ resources: {{ items: {items} }} }};
let resFilter = {{}};
let resView = 'list';
const mockDom = {{ value: '', innerHTML: '' }};
function $() {{ return mockDom; }}
function escapeHtml(s) {{ return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'); }}
{funcs}
const out = {{}};
const EMPTY = {{keyword:'', provider:'', status:'', tag:'', group:'', title:'', updates:''}};
function run(name, patch) {{
  resFilter = Object.assign({{}}, EMPTY, patch || {{}});
  out[name] = filteredResItems().map(i => i.id);
}}
run('all', {{}});
run('group_g1', {{group:'g1'}});
run('group_none', {{group:'__none__'}});
run('group_g2', {{group:'g2'}});
run('status_finished', {{status:'finished'}});
run('tag', {{tag:'日漫'}});
run('provider_quark', {{provider:'quark'}});
run('updates', {{updates:'1'}});
run('keyword_title', {{keyword:'仙逆'}});
run('keyword_path', {{keyword:'剧场版'}});
run('keyword_url', {{keyword:'pan.baidu'}});
run('combo', {{group:'g1', status:'finished'}});
// 模拟 SSE 推送把 state.resources 重新赋成全量：当前分组必须保持不变
resFilter = Object.assign({{}}, EMPTY, {{group:'g1'}});
const before = filteredResItems().map(i => i.id);
state.resources = {{ items: {items} }};
const after = filteredResItems().map(i => i.id);
out.sse_keeps_filter = {{before: before, after: after,
                         same: JSON.stringify(before) === JSON.stringify(after)}};
// 第二行网盘列表：只随当前分组变化，不受搜索词 / 状态影响
function scope(name, patch) {{
  resFilter = Object.assign({{}}, EMPTY, patch || {{}});
  out[name] = resProviderCounts();
}}
scope('providers_all', {{}});
scope('providers_g1', {{group:'g1'}});
scope('providers_g2', {{group:'g2'}});
scope('providers_none', {{group:'__none__'}});
// 切到 g1 后原先选的「其它」在 g1 里没有 → 自动回到「全部」，并同步下拉框
resFilter = Object.assign({{}}, EMPTY, {{group:'g1', provider:'other'}});
mockDom.value = 'other';
syncResProviderFilter();
out.provider_reset = {{provider: resFilter.provider, select: mockDom.value}};
// 该分组确实存在的网盘要保留
resFilter = Object.assign({{}}, EMPTY, {{group:'g1', provider:'quark'}});
mockDom.value = 'quark';
syncResProviderFilter();
out.provider_keep = {{provider: resFilter.provider, select: mockDom.value}};
// 回收站视图不参与校正
resFilter = Object.assign({{}}, EMPTY, {{group:'g1', provider:'other'}});
resView = 'trash';
mockDom.value = 'other';
syncResProviderFilter();
out.provider_trash = resFilter.provider;
// SSE 覆盖全量后，第二行仍按当前分组统计
resFilter = Object.assign({{}}, EMPTY, {{group:'g1'}});
resView = 'list';
state.resources = {{ items: {items} }};
out.sse_scope = resProviderCounts();
// 真正渲染出来的第二行 HTML
function renderBar(name, patch) {{
  resFilter = Object.assign({{}}, EMPTY, patch || {{}});
  resView = 'list';
  mockDom.innerHTML = '';
  renderResProviders();
  out[name] = mockDom.innerHTML;
}}
renderBar('bar_all', {{}});
renderBar('bar_g1', {{group:'g1'}});
renderBar('bar_g1_quark', {{group:'g1', provider:'quark'}});
renderBar('bar_none', {{group:'__none__'}});
// 第三行：按当前分组的标题名统计
function titleScope(name, patch) {{
  resFilter = Object.assign({{}}, EMPTY, patch || {{}});
  out[name] = resTitleCounts();
}}
titleScope('titles_all', {{}});
titleScope('titles_g1', {{group:'g1'}});
titleScope('titles_g1_quark', {{group:'g1', provider:'quark'}});
titleScope('titles_none', {{group:'__none__'}});
run('by_title', {{title:'仙逆'}});
// 切换分组后选中标题失效 → 回到「全部」
resFilter = Object.assign({{}}, EMPTY, {{group:'g1', title:'斗破苍穹'}});
syncResTitleFilter();
out.title_reset = resFilter.title;
resFilter = Object.assign({{}}, EMPTY, {{group:'g1', title:'罗梅莉亚战记'}});
syncResTitleFilter();
out.title_keep = resFilter.title;
resView = 'trash';
resFilter = Object.assign({{}}, EMPTY, {{group:'g1', title:'斗破苍穹'}});
syncResTitleFilter();
out.title_trash = resFilter.title;
resView = 'list';
// 第三行实际渲染的 HTML
function renderTitleBar(name, patch) {{
  resFilter = Object.assign({{}}, EMPTY, patch || {{}});
  resView = 'list';
  mockDom.innerHTML = '';
  renderResTitles();
  out[name] = mockDom.innerHTML;
}}
renderTitleBar('titlebar_g1', {{group:'g1'}});
renderTitleBar('titlebar_g1_pick', {{group:'g1', title:'仙逆'}});
// 同一部作品同时存了夸克 + 百度：聚合成一个标题、2 条
state.resources = {{ items: [
  {{title:'同一部作品', provider:'quark', group:'g1'}},
  {{title:'同一部作品', provider:'baidu', group:'g1'}}
] }};
resFilter = Object.assign({{}}, EMPTY, {{group:'g1'}});
out.dup_title = resTitleCounts();
console.log(JSON.stringify(out));
"""
        proc = subprocess.run(["node", "-e", script], capture_output=True,
                              text=True, encoding="utf-8", timeout=30)
        if proc.returncode != 0:
            raise AssertionError(f"node 执行失败：{proc.stderr}")
        cls.out = json.loads(proc.stdout.strip())

    def test_all(self):
        self.assertEqual(self.out["all"], ["a", "b", "c", "d"])

    def test_group_only_shows_its_own_items(self):
        self.assertEqual(self.out["group_g1"], ["a", "b"])
        self.assertEqual(self.out["group_g2"], ["d"])

    def test_none_group_only_shows_ungrouped(self):
        # 这就是「资源选了分组还出现在未分组」的回归点
        self.assertEqual(self.out["group_none"], ["c"])

    def test_other_filters(self):
        self.assertEqual(self.out["status_finished"], ["b"])
        self.assertEqual(self.out["tag"], ["a", "d"])
        self.assertEqual(self.out["provider_quark"], ["a", "c", "d"])
        self.assertEqual(self.out["updates"], ["b"])

    def test_keyword(self):
        self.assertEqual(self.out["keyword_title"], ["b"])
        self.assertEqual(self.out["keyword_path"], ["b"])   # 命中文件清单
        self.assertEqual(self.out["keyword_url"], ["b"])

    def test_combined(self):
        self.assertEqual(self.out["combo"], ["b"])

    def test_sse_overwrite_keeps_current_group(self):
        row = self.out["sse_keeps_filter"]
        self.assertTrue(row["same"], "SSE 覆盖全量后分组筛选被冲掉了")
        self.assertEqual(row["before"], ["a", "b"])
        self.assertEqual(row["after"], ["a", "b"])

    def test_provider_bar_only_counts_current_group(self):
        # 全库：夸克 3 条 + 百度 1 条
        self.assertEqual(self.out["providers_all"],
                         {"total": 4, "counts": {"quark": 3, "baidu": 1}})
        # 只统计当前分组，且不随搜索词 / 状态变化
        self.assertEqual(self.out["providers_g1"],
                         {"total": 2, "counts": {"quark": 1, "baidu": 1}})
        self.assertEqual(self.out["providers_g2"],
                         {"total": 1, "counts": {"quark": 1}})
        self.assertEqual(self.out["providers_none"],
                         {"total": 1, "counts": {"quark": 1}})

    def test_switch_group_resets_missing_provider(self):
        self.assertEqual(self.out["provider_reset"], {"provider": "", "select": ""})
        self.assertEqual(self.out["provider_keep"],
                         {"provider": "quark", "select": "quark"})
        self.assertEqual(self.out["provider_trash"], "other")

    def test_sse_overwrite_keeps_provider_bar_scope(self):
        self.assertEqual(self.out["sse_scope"]["total"], 2)
        self.assertEqual(self.out["sse_scope"]["counts"],
                         {"quark": 1, "baidu": 1})

    def test_second_row_renders_provider_names_not_tags(self):
        bar = self.out["bar_g1"]
        self.assertIn("网盘", bar)
        self.assertIn("夸克网盘", bar)
        self.assertIn("百度网盘", bar)
        self.assertIn('data-provider="quark"', bar)
        self.assertIn("2 条", bar)
        # 这个分组里没有的网盘不显示；原来的标签行彻底移除
        self.assertNotIn("其它链接", bar)
        self.assertNotIn("data-tag", bar)
        self.assertNotIn("周日", bar)
        self.assertIn("夸克网盘", self.out["bar_none"])
        self.assertIn("1 条", self.out["bar_none"])

    def test_provider_chip_highlights_selected(self):
        self.assertNotIn('class="tag-chip active" data-provider="quark"',
                         self.out["bar_g1"])
        self.assertIn('class="tag-chip active" data-provider="quark"',
                      self.out["bar_g1_quark"])

    def test_third_row_counts_titles_in_current_group(self):
        self.assertEqual(self.out["titles_all"]["total"], 4)
        self.assertEqual(self.out["titles_all"]["order"],
                         ["罗梅莉亚战记", "仙逆", "斗破苍穹", "黑色五叶草"])
        self.assertEqual(self.out["titles_g1"]["order"], ["罗梅莉亚战记", "仙逆"])
        self.assertEqual(self.out["titles_none"]["order"], ["斗破苍穹"])
        # 选了夸克后，第三行只列夸克里的标题
        self.assertEqual(self.out["titles_g1_quark"]["order"], ["罗梅莉亚战记"])
        self.assertEqual(self.out["titles_g1_quark"]["total"], 1)
        self.assertEqual(self.out["by_title"], ["b"])

    def test_same_title_across_providers_merges(self):
        # 批量添加同一部作品的夸克 + 百度两条，第三行聚合成一个标题
        self.assertEqual(self.out["dup_title"]["order"], ["同一部作品"])
        self.assertEqual(self.out["dup_title"]["counts"], {"同一部作品": 2})
        self.assertEqual(self.out["dup_title"]["total"], 2)

    def test_switch_group_resets_missing_title(self):
        self.assertEqual(self.out["title_reset"], "")
        self.assertEqual(self.out["title_keep"], "罗梅莉亚战记")
        self.assertEqual(self.out["title_trash"], "斗破苍穹")

    def test_third_row_renders_title_chips(self):
        bar = self.out["titlebar_g1"]
        self.assertIn("标题", bar)
        self.assertIn("罗梅莉亚战记", bar)
        self.assertIn("仙逆", bar)
        self.assertIn('data-title="仙逆"', bar)
        self.assertNotIn("斗破苍穹", bar)   # 不属于当前分组
        self.assertNotIn("data-tag", bar)
        self.assertIn('class="tag-chip title-chip active" data-title="仙逆"',
                      self.out["titlebar_g1_pick"])


if __name__ == "__main__":
    unittest.main()
