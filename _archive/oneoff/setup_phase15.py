# -*- coding: utf-8 -*-
r'''setup_phase15.py - Sprint11.5: difficulty 接入 + 推荐难度优先 + 构建器同学段优先
    补丁1 data_loader:    Topic.difficulty 字段 + _k12_difficulty 解析 + 接线
    补丁2 graph_service:  边界候选排序 难度->杠杆->年级->key
    补丁3 build_inferred_links.py: 候选选择 同科同学段>同科>全局 (v3)
    补丁4 inspect_inferred_quality.py: D段形状守卫
    幂等 | 逐文件compile | 子进程冒烟'''
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def find_one(lines, anchor, what):
    idxs = [i for i, ln in enumerate(lines) if ln.strip() == anchor]
    if len(idxs) != 1:
        print("[ABORT] " + what + " 锚点异常(" + str(len(idxs)) + "处): "
              + anchor)
        sys.exit(1)
    return idxs[0]


def insert_after(lines, anchor, new, what=""):
    i = find_one(lines, anchor, what)
    lines[i + 1:i + 1] = new
    return lines


def insert_before(lines, anchor, new, what=""):
    i = find_one(lines, anchor, what)
    lines[i:i] = new
    return lines


def replace_span(lines, anchor, span, new, expect, what=""):
    i = find_one(lines, anchor, what)
    cur = [lines[i + j].strip() for j in range(span)]
    if cur != expect:
        print("[ABORT] " + what + " 上下文不符: " + str(cur))
        sys.exit(1)
    lines[i:i + span] = new
    return lines


def save(p, text):
    compile(text, str(p), "exec")
    p.write_text(text, encoding="utf-8")
    print("[OK] " + p.name)


# ---- 补丁1: data_loader ----
dl = ROOT / "backend" / "data_loader.py"
src = dl.read_text(encoding="utf-8")
if "def _k12_difficulty" in src:
    print("[SKIP] data_loader")
else:
    lines = src.splitlines()
    lines = insert_after(lines, "evidence: List[str] = field(default_factory=list)",
                         ["    difficulty: float = 0.5"], "Topic字段")
    helper = ['def _k12_difficulty(t: dict) -> float:',
              '    """Sprint11.5: annotations.difficulty.value(0~1), 缺失/非法回退0.5。"""',
              '    raw = t.get("annotations")',
              '    try:',
              '        ann = json.loads(raw) if isinstance(raw, str) else (raw or {})',
              '        v = (ann.get("difficulty") or {}).get("value")',
              '        if isinstance(v, (int, float)) and not isinstance(v, bool):',
              '            return max(0.0, min(1.0, float(v)))',
              '    except (ValueError, TypeError):',
              '        pass',
              '    return 0.5',
              '', '']
    lines = insert_before(lines, "def _to_k12_topic(t: dict, band: str) -> Topic:",
                          helper, "helper")
    lines = insert_after(lines, 'description=str(t.get("summary", "")),',
                         ['        difficulty=_k12_difficulty(t),'], "接线")
    save(dl, "\n".join(lines) + "\n")

# ---- 补丁2: graph_service ----
gs = ROOT / "backend" / "graph_service.py"
src = gs.read_text(encoding="utf-8")
if "nodes[key].difficulty" in src:
    print("[SKIP] graph_service")
else:
    lines = src.splitlines()
    lines = replace_span(
        lines, "cands.sort(key=lambda key: (-unlock_of(key),", 3,
        ["                cands.sort(key=lambda key: (self.graph.nodes[key].difficulty,",
         "                                            -unlock_of(key),",
         "                                            self.graph.nodes[key].grade,",
         "                                            key))"],
        ["cands.sort(key=lambda key: (-unlock_of(key),",
         "self.graph.nodes[key].grade,", "key))"], "排序")
    soft = [i for i, ln in enumerate(lines) if "解锁杠杆/年级" in ln]
    if len(soft) == 1:
        lines[soft[0]] = ("          2. 否则取该科目边界节点中 难度低优先, "
                          "同难度取解锁杠杆高者")
    save(gs, "\n".join(lines) + "\n")

# ---- 补丁3: build_inferred_links.py -> v3 同学段优先 ----
bl = ROOT / "build_inferred_links.py"
src = bl.read_text(encoding="utf-8")
if "_pick(cands, subj, band)" in src:
    print("[SKIP] build_inferred_links")
else:
    lines = src.splitlines()
    pick_fn = ['def _pick(cands, subj, band):',
               '    """同科同学段 > 同科 > 全局; 同层按 key 稳定排序"""',
               '    for tier in ([c for c in cands if c[1] == subj and c[2] == band],',
               '                 [c for c in cands if c[1] == subj],',
               '                 cands):',
               '        if tier:',
               '            return sorted(tier, key=lambda c: c[0])[0][0]',
               '    return None',
               '', '']
    lines = insert_before(lines, "def kosaraju(nodes, adj):", pick_fn, "_pick")
    lines = replace_span(lines, "exact_idx[n].append((key, t.subject))", 1,
                         ["            exact_idx[n].append((key, t.subject, t.book))"],
                         ["exact_idx[n].append((key, t.subject))"], "exact索引")
    lines = replace_span(lines, "relax_idx[relaxed(n)].append((key, t.subject))", 1,
                         ["            relax_idx[relaxed(n)].append((key, t.subject, t.book))"],
                         ["relax_idx[relaxed(n)].append((key, t.subject))"], "relax索引")
    lines = insert_after(lines, "my_subj = g.nodes[to_key].subject",
                         ["        my_band = g.nodes[to_key].book"], "my_band")
    lines = replace_span(lines, "same = [c for c in cands if c[1] == my_subj]", 2,
                         ["                hit = _pick(cands, my_subj, my_band)"],
                         ["same = [c for c in cands if c[1] == my_subj]",
                          "hit = (same or sorted(cands, key=lambda c: c[0]))[0][0]"],
                         "exact挑选")
    lines = replace_span(lines, "same2 = [c for c in lst if c[1] == my_subj]", 2,
                         ["                        pk = _pick(lst, my_subj, my_band)"],
                         ["same2 = [c for c in lst if c[1] == my_subj]",
                          "pick = (same2 or sorted(lst, key=lambda c: c[0]))[0]"],
                         "contains挑选1")
    lines = replace_span(lines, "if best is None or len(nn) < len(best[0]):", 2,
                         ["                        if pk and (best is None or len(nn) < len(best[0])):",
                          "                            best = (nn, pk)"],
                         ["if best is None or len(nn) < len(best[0]):",
                          "best = (nn, pick[0])"], "contains挑选2")
    lines = replace_span(lines, '"version": 2, "generator": "build_inferred_links.py",', 1,
                         ['    "version": 3, "generator": "build_inferred_links.py",'],
                         ['"version": 2, "generator": "build_inferred_links.py",'],
                         "版本号")
    save(bl, "\n".join(lines) + "\n")

# ---- 补丁4: inspect_inferred_quality.py D段形状守卫 ----
iq = ROOT / "inspect_inferred_quality.py"
src = iq.read_text(encoding="utf-8")
if "kps_d" in src:
    print("[SKIP] inspect_inferred_quality")
else:
    lines = src.splitlines()
    lines = replace_span(lines, 'for kp in d.get("knowledge_points") or []:', 1,
                         ["    kps_d = (d.get(\"knowledge_points\") or [])"
                          " if isinstance(d, dict) else []",
                          "    for kp in kps_d:"],
                         ['for kp in d.get("knowledge_points") or []:'], "D段守卫")
    save(iq, "\n".join(lines) + "\n")

# ---- 冒烟 ----
r = subprocess.run(
    [sys.executable, "-c",
     "from backend import data_loader;"
     "g = data_loader.get_graph();"
     "nd = sum(1 for t in g.nodes.values() if abs(t.difficulty - 0.5) > 1e-9);"
     "inf = sum(1 for e in g.edges if e.strength == 'inferred');"
     "print('diff_nondefault=' + str(nd) + ' inferred=' + str(inf))"],
    cwd=str(ROOT), capture_output=True, text=True)
tail = [ln for ln in (r.stdout or "").strip().splitlines() if ln]
print("[SMOKE] " + (tail[-1] if tail
                    else "(无输出) " + (r.stderr or "")[-300:]))
print("下一步: python build_inferred_links.py && python -m pytest tests/ -q")
