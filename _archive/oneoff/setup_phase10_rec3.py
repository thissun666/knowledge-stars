# -*- coding: utf-8 -*-
r'''setup_phase10_rec3.py - 旧推荐测试同步到"每科一条"新契约(仅改测试, 不动实现)'''
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent

NEW_EMPTY = '''def test_recommend_paths_empty_progress(svc):
    # 新契约: 每科目一条。夹具全部同科目 -> 恰好 1 条(旧 top-k 语义已废弃)
    steps = svc.recommend_paths({}, k=3)
    assert len(steps) == 1
    subjects = [s["subject"] for s in steps]
    assert len(subjects) == len(set(subjects))
    assert steps[0]["key"] == "marble:mt_A"
    assert steps[0]["learning"] is False


'''

NEW_MASTERED = '''def test_recommend_paths_after_mastered(svc):
    # 新契约: 该科目只出一个名额, 掌握 mt_A 后由杠杆更高的边界节点接任
    # (解锁传播由 test_recommend_per_subject.py::test_switch_after_mastery 覆盖)
    steps = svc.recommend_paths({"marble:mt_A": "mastered"})
    keys = [s["key"] for s in steps]
    assert "marble:mt_A" not in keys
    assert keys == ["pep:7-up:mt_ch1_001"]


'''


def main():
    print("项目根: " + str(ROOT))
    p = ROOT / "tests" / "test_graph_service.py"
    src = p.read_text(encoding="utf-8")

    if 'assert keys == ["pep:7-up:mt_ch1_001"]' in src:
        print("[SKIP] 已同步过")
        raise SystemExit(0)

    count = 0
    for name, new_body in (
            ("test_recommend_paths_empty_progress", NEW_EMPTY),
            ("test_recommend_paths_after_mastered", NEW_MASTERED)):
        pat = re.compile(
            r"def " + name + r"\(svc\):.*?(?=\ndef |\Z)", re.S)
        if not pat.search(src):
            print("[FAIL] 未找到 " + name + ", 中止")
            raise SystemExit(1)
        src = pat.sub(new_body, src, count=1)
        count += 1

    try:
        compile(src, "test_graph_service.py", "exec")
    except SyntaxError as e:
        print("[FAIL] 改写后语法校验失败: " + str(e))
        raise SystemExit(1)
    p.write_text(src, encoding="utf-8")
    print("[OK] 两个旧测试已同步到新契约 (替换 " + str(count) + " 处, 已编译校验)")
    print("验收:")
    print("  python -m pytest tests/ -q   (预期 72 passed)")
    print("=" * 56)


if __name__ == "__main__":
    main()
