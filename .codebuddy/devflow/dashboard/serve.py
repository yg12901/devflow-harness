#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""devflow 实时看板服务。

    python3 .codebuddy/devflow/dashboard/serve.py [--port 8770]

只依赖标准库，不需要安装任何东西。提供：

    GET /                        看板页面
    GET /api/runs                所有运行概览
    GET /api/runs/<run_id>       单次运行详情
    GET /api/learnings           经验库现状
    GET /api/artifacts/<run_id>  某次运行的产物清单
    GET /api/artifacts/<run_id>/<文件名>   产物内容
"""

import argparse
import json
import os
import sys
from wsgiref.simple_server import make_server

HERE = os.path.dirname(os.path.abspath(__file__))
DEVFLOW_DIR = os.path.dirname(HERE)
sys.path.insert(0, DEVFLOW_DIR)

from engine import core, learning  # noqa: E402
from engine.state import RunState  # noqa: E402


def json_response(payload, status="200 OK"):
    body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    headers = [
        ("Content-Type", "application/json; charset=utf-8"),
        ("Content-Length", str(len(body))),
        ("Cache-Control", "no-store"),
        ("Access-Control-Allow-Origin", "*"),
    ]
    return status, headers, [body]


def text_response(body, content_type="text/html; charset=utf-8", status="200 OK"):
    data = body.encode("utf-8") if isinstance(body, str) else body
    headers = [
        ("Content-Type", content_type),
        ("Content-Length", str(len(data))),
        ("Cache-Control", "no-store"),
    ]
    return status, headers, [data]


def safe_join(base, *parts):
    """拼路径并确认结果没跑出 base 之外，防目录穿越。"""
    target = os.path.abspath(os.path.join(base, *parts))
    if not target.startswith(os.path.abspath(base) + os.sep):
        return None
    return target


def handle(path):
    if path in ("/", "/index.html"):
        return text_response(core.read_text(os.path.join(HERE, "index.html")))

    if path == "/api/runs":
        return json_response({"runs": RunState.list_runs()})

    if path == "/api/learnings":
        entries = learning.load_entries()
        return json_response({
            "overview": learning.stats_overview(entries),
            "entries": [{
                "id": e["id"], "priority": e.get("priority"),
                "status": e.get("status"), "stage": e.get("stage"),
                "pattern": e.get("pattern"), "fix": e.get("fix"),
                "tags": e.get("tags", []), "stats": e.get("stats", {}),
            } for e in entries],
        })

    if path.startswith("/api/runs/"):
        run_id = path[len("/api/runs/"):].strip("/")
        try:
            workflow = core.load_workflow()
            state = RunState.load(run_id)
        except core.ConfigError as exc:
            return json_response({"error": str(exc)}, "404 Not Found")
        payload = state.summary(workflow)
        payload["events"] = state.data.get("events", [])[-40:]
        payload["transitions"] = state.data.get("transitions", [])
        payload["context"] = state.data.get("context", {})
        return json_response(payload)

    if path.startswith("/api/artifacts/"):
        rest = path[len("/api/artifacts/"):].strip("/")
        if not rest:
            return json_response({"error": "缺少 run_id"}, "400 Bad Request")
        parts = rest.split("/", 1)
        run_id = parts[0]
        base = os.path.join(core.run_dir(run_id), "artifacts")
        if not os.path.isdir(base):
            return json_response({"error": "产物目录不存在"}, "404 Not Found")

        if len(parts) == 1:
            files = []
            for root, _dirs, names in os.walk(base):
                for name in sorted(names):
                    full = os.path.join(root, name)
                    files.append({
                        "name": os.path.relpath(full, base),
                        "size": os.path.getsize(full),
                    })
            return json_response({"run_id": run_id, "files": files})

        target = safe_join(base, parts[1])
        if not target or not os.path.isfile(target):
            return json_response({"error": "文件不存在"}, "404 Not Found")
        return text_response(core.read_text(target), "text/plain; charset=utf-8")

    return json_response({"error": "未知路径 %s" % path}, "404 Not Found")


def application(environ, start_response):
    try:
        status, headers, body = handle(environ.get("PATH_INFO", "/"))
    except Exception as exc:  # 看板挂掉不应该影响流水线，兜住所有异常
        status, headers, body = json_response(
            {"error": "%s: %s" % (type(exc).__name__, exc)}, "500 Internal Server Error")
    start_response(status, headers)
    return body


def main():
    parser = argparse.ArgumentParser(description="devflow 实时看板")
    parser.add_argument("--port", type=int, default=8770)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()

    server = make_server(args.host, args.port, application)
    sys.stderr.write("devflow 看板已启动：http://%s:%d/\n" % (args.host, args.port))
    sys.stderr.write("按 Ctrl-C 停止\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        sys.stderr.write("\n已停止\n")


if __name__ == "__main__":
    main()
