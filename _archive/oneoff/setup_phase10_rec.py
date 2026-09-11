# -*- coding: utf-8 -*-
r'''setup_phase10_rec.py - 推荐路径改造: 每科目一条, 学习中锚定, 完成才更换
    后端: recommend_paths 重写(锚定+分组选取) | k上限 10->40
    前端: 弹窗拉全科目 + 文案 + 继续学习区分"继续/建议" | v=12
    测试: +3 条(每科一条/学习中锚定/掌握后更换)'''
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CURLY = "".join(chr(c) for c in (0x201C, 0x201D, 0x2018, 0x2019))
BAD = chr(96) * 3

NEW_FN = '''    def recommend_paths(self, progress: Dict[str, str],
                        k: int = 30) -> List[dict]:
        """学习路径推荐(纯图算法): 每科目固定一条, 完成才更换。

        每科目选取:
          1. 有"学习中"节点 -> 锚定该节点(无论是否边界), 完成前不更换
          2. 否则取该科目边界节点中 解锁杠杆/年级 最优者
        科目间: 学习中条目置顶, 其余按解锁杠杆降序; 确定性输出可测试。
        """
        boundary = {key for key in self.graph.nodes
                    if self.is_boundary(key, progress)}
        cache: Dict[str, int] = {}

        def unlock_of(key: str) -> int:
            if key not in cache:
                cache[key] = sum(1 for d in self.descendants(key)
                                 if progress.get(d) != "mastered")
            return cache[key]

        by_subject: Dict[str, List[str]] = {}
        for key, t in self.graph.nodes.items():
            by_subject.setdefault(t.subject, []).append(key)

        picked: List[dict] = []
        for subject in sorted(by_subject):
            keys = by_subject[subject]
            entry = None
            learning = sorted(
                (key for key in keys if progress.get(key) == "learning"),
                key=lambda key: (-unlock_of(key), key))
            if learning:
                key = learning[0]
                t = self.graph.nodes[key]
                if key in boundary:
                    u = unlock_of(key)
                    reason = ("学习中 · 前置已全部掌握, 掌握后可解锁 "
                              + str(u) + " 个知识点(含间接)")
                else:
                    u = 0
                    gap = sum(1 for p in self.graph.adj_in.get(key, [])
                              if progress.get(p) != "mastered")
                    reason = ("学习中 · 尚有 " + str(gap)
                              + " 个前置未掌握, 掌握前置后可解锁后续")
                entry = {"key": key, "name": t.name, "subject": subject,
                         "domain": t.domain, "grade": t.grade,
                         "unlock_count": u, "learning": True,
                         "reason": reason}
            else:
                cands = [key for key in keys if key in boundary]
                if not cands:
                    continue
                cands.sort(key=lambda key: (-unlock_of(key),
                                            self.graph.nodes[key].grade,
                                            key))
                key = cands[0]
                t = self.graph.nodes[key]
                u = unlock_of(key)
                entry = {"key": key, "name": t.name, "subject": subject,
                         "domain": t.domain, "grade": t.grade,
                         "unlock_count": u, "learning": False,
                         "reason": ("前置已全部掌握, 掌握后可解锁 "
                                    + str(u) + " 个知识点(含间接)"
                                    if u else "前置已全部掌握, 可直接学习")}
            picked.append(entry)
        picked.sort(key=lambda s: (0 if s["learning"] else 1,
                                   -s["unlock_count"], s["grade"],
                                   s["subject"], s["key"]))
        for i, s in enumerate(picked[:max(1, k)]):
            s["order"] = i + 1
        return picked[:max(1, k)]
'''

TESTS = '''# -*- coding: utf-8 -*-
"""每科一条推荐: 选取/学习中锚定/掌握后更换(合成小图)。"""
from backend import data_loader
from backend.graph_service import GraphService


def mini_graph():
    g = data_loader.Graph()
    for kid, name in (("a1", "加法"), ("a2", "乘法"), ("a3", "方程")):
        g.add_node(data_loader.Topic(
            key="k12:" + kid, id=kid, source="k12", book="小学",
            name=name, type="", subject="数学", domain="数学", grade=3))
    for pre, nxt in (("a1", "a2"), ("a2", "a3")):
        g.add_edge(data_loader.Edge(prereq_key="k12:" + pre,
                                    topic_key="k12:" + nxt))
    for kid, name in (("b1", "字母"), ("b2", "单词")):
        g.add_node(data_loader.Topic(
            key="k12:" + kid, id=kid, source="k12", book="小学",
            name=name, type="", subject="英语", domain="英语", grade=3))
    g.add_edge(data_loader.Edge(prereq_key="k12:b1", topic_key="k12:b2"))
    return g


def test_one_per_subject():
    gs = GraphService(mini_graph())
    steps = gs.recommend_paths({}, k=30)
    assert [s["key"] for s in steps] == ["k12:a1", "k12:b1"]
    assert all(not s["learning"] for s in steps)


def test_learning_anchor_locks_subject():
    gs = GraphService(mini_graph())
    steps = gs.recommend_paths({"k12:a2": "learning"}, k=30)
    assert steps[0]["key"] == "k12:a2"
    assert steps[0]["learning"] is True
    assert "前置未掌握" in steps[0]["reason"]
    assert steps[1]["key"] == "k12:b1"


def test_switch_after_mastery():
    gs = GraphService(mini_graph())
    steps = gs.recommend_paths({"k12:a1": "mastered"}, k=30)
    assert [s["key"] for s in steps] == ["k12:a2", "k12:b1"]
'''


def main():
    print("项目根: " + str(ROOT))
    ok = True
    gs_p = ROOT / "backend" / "graph_service.py"
    src = gs_p.read_text(encoding="utf-8")
    start = src.find("    def recommend_paths(self, progress: Dict[str, str],")
    endm = "    def compute_boundary(self"
    end = src.find(endm, start)
    if start < 0 or end < 0:
        print("[FAIL] graph_service 锚点未找到, 中止")
        ok = False
    for piece in (NEW_FN, TESTS):
        if BAD in piece or any(x in piece for x in CURLY):
            print("[FAIL] 注入内容自检未过, 中止")
            ok = False
            break
    if ok:
        gs_p.write_text(src[:start] + NEW_FN + "\n\n" + src[end:],
                        encoding="utf-8")
        print("[OK] recommend_paths -> 每科一条/学习中锚定/完成才更换")

    if ok:
        rt_p = ROOT / "backend" / "routes" / "graph.py"
        rt = rt_p.read_text(encoding="utf-8")
        if "k = max(1, min(10, k))" in rt:
            rt = rt.replace("k = max(1, min(10, k))",
                            "k = max(1, min(40, k))")
            rt_p.write_text(rt, encoding="utf-8")
            print("[OK] 路由 k 上限 10 -> 40")
        else:
            print("[WARN] 路由 k 上限锚点未找到(弹窗仍可用, 仅上限旧值)")

    if ok:
        mj_p = ROOT / "web" / "js" / "main.js"
        mj = mj_p.read_text(encoding="utf-8")
        n = mj.count("API.recommend(5, currentBand)")
        if n:
            mj = mj.replace("API.recommend(5, currentBand)",
                            "API.recommend(30, currentBand)")
            print("[OK] 弹窗推荐拉全科目 (替换 " + str(n) + " 处)")
        else:
            print("[WARN] 未找到 API.recommend(5, currentBand), 弹窗可能仍截断, 回报我")
        if "由图谱算法直出(解锁杠杆排序)" in mj:
            mj = mj.replace("由图谱算法直出(解锁杠杆排序)",
                            "由图谱算法直出(每科一条·完成才更换)")
            print("[OK] 弹窗 meta 文案已更新")
        else:
            print("[WARN] meta 文案锚点未找到, 仅文案未变")
        old_toast = ('UI.toast("图谱建议现在学: " + s.name + " · "'
                     ' + s.reason, "ok");')
        new_toast = ('UI.toast((s.learning ? "继续学习: " : "图谱建议现在学: ")'
                     ' +\n        s.name + " · " + s.reason, "ok");')
        if old_toast in mj:
            mj = mj.replace(old_toast, new_toast)
            print("[OK] 继续学习 toast 区分 锚定/新推荐")
        else:
            print("[WARN] toast 锚点未找到, 文案未变")
        mj_p.write_text(mj, encoding="utf-8")

    if ok:
        ap_p = ROOT / "web" / "js" / "api.js"
        ap = ap_p.read_text(encoding="utf-8")
        if "(k || 5)" in ap:
            ap = ap.replace("(k || 5)", "(k || 30)")
            ap_p.write_text(ap, encoding="utf-8")
            print("[OK] api.js 默认 k -> 30")
        else:
            print("[WARN] api.js k 默认锚点未找到")

    if ok:
        (ROOT / "tests" / "test_recommend_per_subject.py").write_text(
            TESTS, encoding="utf-8")
        print("[OK] 新增测试 test_recommend_per_subject.py (+3)")

    if ok:
        html_p = ROOT / "web" / "index.html"
        html = html_p.read_text(encoding="utf-8")
        import re
        for name in ("api.js", "sim.js", "render.js", "ui.js", "main.js"):
            html = re.sub(r'src="/js/' + name + r'(\?v=\d+)?"',
                          'src="/js/' + name + '?v=12"', html)
        html_p.write_text(html, encoding="utf-8")
        print("[OK] 缓存版本 -> v=12")

    if ok:
        print("验收:")
        print("  python -m pytest tests/ -q   (预期 72 passed; 若旧推荐测试挂, 贴我)")
        print("  重启服务+刷新, 三个学段各开一次推荐路径:")
        print("    小学 2-3 条(语/数/英各一) | 初中 10 条 | 高中 6 条(含数学)")
        print("    标一个学习中 -> 该科置顶锁定; 掌握它 -> 下次打开换下一条")
        print("=" * 56)


if __name__ == "__main__":
    main()
