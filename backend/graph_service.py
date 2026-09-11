# -*- coding: utf-8 -*-
"""图谱服务: 拓扑排序、祖先/后代链(记忆化)、边界节点(全量+增量)、
学习路径推荐(纯图算法, 差异化核心: 推荐不靠AI编, 靠图谱算)。
"""
import logging
from collections import deque
from typing import Dict, List, Optional, Set, Tuple

from backend import data_loader

logger = logging.getLogger("app.graph")


class GraphService:
    def __init__(self, graph: data_loader.Graph):
        self.graph = graph
        self._topo: Optional[List[str]] = None
        self._anc: Dict[str, Set[str]] = {}
        self._desc: Dict[str, Set[str]] = {}
        self._boundary: Optional[Set[str]] = None

    def topo_order(self) -> List[str]:
        if self._topo is None:
            indeg = {k: 0 for k in self.graph.nodes}
            for e in self.graph.edges:
                indeg[e.topic_key] += 1
            q = deque(sorted(k for k, d in indeg.items() if d == 0))
            order: List[str] = []
            while q:
                k = q.popleft()
                order.append(k)
                for m in sorted(self.graph.adj_out.get(k, [])):
                    indeg[m] -= 1
                    if indeg[m] == 0:
                        q.append(m)
            self._topo = order
            logger.debug("拓扑排序完成: %d/%d 节点", len(order),
                         len(self.graph.nodes))
        return self._topo

    def is_dag(self) -> bool:
        return len(self.topo_order()) == len(self.graph.nodes)

    def descendants(self, key: str) -> Set[str]:
        if key not in self._desc:
            seen: Set[str] = set()
            stack = [key]
            while stack:
                cur = stack.pop()
                for nxt in self.graph.adj_out.get(cur, ()):
                    if nxt not in seen:
                        seen.add(nxt)
                        stack.append(nxt)
            self._desc[key] = seen
        return self._desc[key]

    def ancestors(self, key: str) -> Set[str]:
        if key not in self._anc:
            seen: Set[str] = set()
            stack = [key]
            while stack:
                cur = stack.pop()
                for pre in self.graph.adj_in.get(cur, ()):
                    if pre not in seen:
                        seen.add(pre)
                        stack.append(pre)
            self._anc[key] = seen
        return self._anc[key]

    def is_boundary(self, key: str, progress: Dict[str, str]) -> bool:
        if progress.get(key) == "mastered":
            return False
        pres = self.graph.adj_in.get(key, [])
        return all(progress.get(p) == "mastered" for p in pres)

    def compute_boundary(self, progress: Dict[str, str]) -> Set[str]:
        b = {k for k in self.graph.nodes if self.is_boundary(k, progress)}
        self._boundary = set(b)
        logger.debug("全量边界重算: %d 个", len(b))
        return b

    def recommend_paths(self, progress: Dict[str, str],
                        k: int = 30) -> List[dict]:
        """学习路径推荐(纯图算法): 每科目固定一条, 完成才更换。

        每科目选取:
          1. 有"学习中"节点 -> 锚定该节点(无论是否边界), 完成前不更换
          2. 否则取该科目边界节点中 优先有下游(可解锁>0)者, 其中难度低优先;
             全科目边界均无下游时, 回退纯难度排序
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
                cands.sort(key=lambda key: (unlock_of(key) <= 0,
                                            self.graph.nodes[key].difficulty,
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
                                    if u else "前置已全部掌握, 可直接学习(暂无后续节点)")}
            picked.append(entry)
        picked.sort(key=lambda s: (0 if s["learning"] else 1,
                                   -s["unlock_count"], s["grade"],
                                   s["subject"], s["key"]))
        for i, s in enumerate(picked[:max(1, k)]):
            s["order"] = i + 1
        return picked[:max(1, k)]


    def apply_change(self, progress: Dict[str, str],
                     changed_keys: List[str]) -> Tuple[Set[str], List[str], List[str]]:
        if self._boundary is None:
            self.compute_boundary(progress)
        before = set(self._boundary or set())
        candidates: Set[str] = set()
        for c in changed_keys:
            candidates.add(c)
            candidates |= self.descendants(c)
        for c in candidates:
            if c in self.graph.nodes and self.is_boundary(c, progress):
                self._boundary.add(c)
            else:
                self._boundary.discard(c)
        newly = sorted(self._boundary - before)
        lost = sorted(before - self._boundary)
        if newly or lost:
            logger.debug("边界增量: + %s - %s", newly, lost)
        return set(self._boundary), newly, lost

    def serialize(self, progress: Dict[str, str],
                  boundary: Set[str]) -> dict:
        nodes = []
        for key in sorted(self.graph.nodes):
            t = self.graph.nodes[key]
            nodes.append({
                "key": t.key, "id": t.id, "source": t.source,
                "book": t.book, "name": t.name, "type": t.type,
                "subject": t.subject, "domain": t.domain,
                "grade": t.grade, "description": t.description,
            })
        edges = [{"prereq": e.prereq_key, "topic": e.topic_key,
                  "strength": e.strength} for e in self.graph.edges]
        return {
            "nodes": nodes,
            "edges": edges,
            "progress": progress,
            "boundary": sorted(boundary),
            "stats": data_loader.stats(self.graph),
        }
