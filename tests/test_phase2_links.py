# -*- coding: utf-8 -*-
from pathlib import Path

import pytest

import backend.data_loader as dl


def test_phase2_links_loaded():
    p = Path(__file__).resolve().parent.parent / "data" / "phase2_links.json"
    if not p.exists():
        pytest.skip("phase2_links.json 未生成(先跑 build_phase2_edges.py)")
    g = dl.get_graph()
    n = sum(1 for e in g.edges if e.reason == "phase2")
    assert n >= 200, "phase2边应>=200, 实际 " + str(n)
    assert all(e.strength == "inferred"
               for e in g.edges if e.reason == "phase2")
