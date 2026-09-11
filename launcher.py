# -*- coding: utf-8 -*-
"""launcher.py - 双击入口: 起服务 + 自动开浏览器. 开发态: python launcher.py"""
import threading
import webbrowser

import uvicorn

from backend import config
from backend.main import app

if __name__ == "__main__":
    host = getattr(config, "HOST", "127.0.0.1")
    port = int(getattr(config, "PORT", 8000))
    url = "http://127.0.0.1:%d" % port
    threading.Timer(1.5, webbrowser.open, args=(url,)).start()
    uvicorn.run(app, host=host, port=port, log_level="info")
