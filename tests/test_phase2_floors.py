# -*- coding: utf-8 -*-
# v3: floor0.40 + containment minlen6 + 真实误例回归测试
from build_phase2_edges import accept, floor_of, tarjan_scc


def test_floor_by_subject():
    assert floor_of('英语') == 0.5
    assert floor_of('语文') == 0.5
    assert floor_of('化学') == 0.4
    assert floor_of('物理') == 0.4


def _r(via, sim, subj, nid='x', **kw):
    d = {'via': via, 'sim': sim, 'subject': subj, 'id': nid}
    d.update(kw)
    return d


def test_accept_rules():
    assert accept(_r('llm', 0.45, '化学'))
    assert accept(_r('llm', 0.4, '化学'))
    assert not accept(_r('llm', 0.39, '化学'))
    assert not accept(_r('llm', 0.45, '英语'))
    assert accept(_r('llm-low', 0.6, '化学'))
    assert not accept(_r('null', 0.9, '化学'))
    assert not accept(_r('cand-empty', 0.9, '化学'))
    assert accept(_r('llm', 0.9, '化学', nid=None))


def test_containment_minlen6():
    assert accept(_r('llm-low', 0.29, '物理',
                     raw='牛顿第二定律', ret='牛顿第二定律的数学表达式与单位制约定'))
    assert not accept(_r('llm-low', 0.24, '英语',
                       raw='一般过去时', ret='一般过去时一般疑问句（Did+主语+动词原形）'))
    assert not accept(_r('llm-low', 0.18, '语文',
                       raw='开篇策略', ret='开篇策略（开门见山／描写入题／设悬念）'))
    assert not accept(_r('llm-low', 0.29, '化学',
                       raw='共价键的基本概念', ret='共价键'))


def test_tarjan_cycle():
    sccs = tarjan_scc(['a', 'b', 'c'], {'a': ['b'], 'b': ['a']})
    big = [c for c in sccs if len(c) > 1]
    assert len(big) == 1 and set(big[0]) == {'a', 'b'}
