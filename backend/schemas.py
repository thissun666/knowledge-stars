# -*- coding: utf-8 -*-
"""统一响应封装: 所有 API 一律返回 {code, data, msg}。

code 约定:
  0   成功
  1   通用业务错误
  2   未知知识点 topic_key
  3   非法状态值 status
  422 请求参数校验失败(由 main.py 异常处理器转换, HTTP 仍为 200)
"""


def ok(data=None, msg: str = "ok") -> dict:
    return {"code": 0, "data": data, "msg": msg}


def err(msg: str = "error", code: int = 1) -> dict:
    return {"code": code, "data": None, "msg": msg}
