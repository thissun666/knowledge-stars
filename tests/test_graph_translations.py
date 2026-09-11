# -*- coding: utf-8 -*-
"""translations附件: 缓存命中过滤+坏JSON容错(纯函数)。"""
import json

from backend.routes.graph import _translations_for


class _FakePS:
    def __init__(self, store):
        self.store = store

    def cache_get(self, kind, key):
        assert kind == "translate"
        return self.store.get(key)


def test_hit_miss_and_bad_json():
    ps = _FakePS({
        "k12:a1": json.dumps({"name": "加法", "description": "d1"},
                             ensure_ascii=False),
        "k12:a2": "坏JSON{{{",
    })
    out = _translations_for(["k12:a1", "k12:a2", "k12:a3"], ps)
    assert out == {"k12:a1": {"name": "加法", "description": "d1"}}
