# -*- coding: utf-8 -*-
"""FastAPI 入口: 路由 + 静态托管同源(免 CORS/代理), 无 lifespan 副作用。

服务单例在 backend/state 中惰性初始化, 首次请求才加载数据,
因此 TestClient/uvicorn 启动均无后台线程, 规避竞态。
"""
import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

import paths
from backend import schemas
from backend.routes import ai as ai_routes
from backend.routes import graph as graph_routes
from backend.routes import progress as progress_routes

from logging_setup import setup_logging
from backend import config

logger = setup_logging(config.LOG_LEVEL)

app = FastAPI(title="知识点星图", version="0.4.0")

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1):\d+",
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc):
    """参数校验失败也走统一格式, 前端只认 code 字段。"""
    return JSONResponse(status_code=200,
                        content=schemas.err("参数校验失败", code=422))


@app.get("/api/health", tags=["meta"])
def health():
    return schemas.ok({"status": "up"})


app.include_router(graph_routes.router, prefix="/api")
app.include_router(progress_routes.router, prefix="/api")
app.include_router(ai_routes.router, prefix="/api")

# 静态托管放最后: 未命中 API 的路径回落到 web/, index.html 作为首页
app.mount("/", StaticFiles(directory=paths.WEB_DIR, html=True), name="web")

logger.info("应用已装配: web=%s", paths.WEB_DIR)
