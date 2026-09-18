# -*- coding: utf-8 -*-
"""仓库地图预热。

针对面试官提的「靠人维护业务逻辑、判断具体在哪里写代码」这个卡点。

Agent 不知道该去哪改代码，根因是每次会话都从零开始摸索仓库，而摸索过程
既慢又容易臆测。这里在流水线启动前扫一次仓库，把「模块在哪、什么语言、
谁是热点文件、入口在哪」固化成一份 markdown，后续各阶段按需精准加载，
而不是每次 grep 一遍全仓。

实现刻意保持轻量：纯文件系统遍历 + git log 统计，不依赖任何语言的
解析器，因此换技术栈不用改代码。
"""

import os
from collections import Counter

from . import core

LANGUAGE_BY_EXT = {
    ".c": "C", ".h": "C/C++ 头文件", ".cc": "C++", ".cpp": "C++", ".cxx": "C++",
    ".hpp": "C++ 头文件", ".go": "Go", ".py": "Python", ".java": "Java",
    ".kt": "Kotlin", ".js": "JavaScript", ".ts": "TypeScript", ".tsx": "TypeScript",
    ".rs": "Rust", ".proto": "Protobuf", ".sql": "SQL", ".sh": "Shell",
    ".rb": "Ruby", ".php": "PHP", ".scala": "Scala", ".swift": "Swift",
}

BUILD_MARKERS = {
    "CMakeLists.txt": "CMake", "Makefile": "Make", "BUILD": "Bazel/Blade",
    "BUILD.bazel": "Bazel", "BLADE_ROOT": "Blade", "go.mod": "Go Modules",
    "pom.xml": "Maven", "build.gradle": "Gradle", "package.json": "npm",
    "Cargo.toml": "Cargo", "pyproject.toml": "Python", "setup.py": "Python",
    "requirements.txt": "Python", "conanfile.txt": "Conan",
}

IGNORED_DIRS = {
    ".git", ".svn", "node_modules", "__pycache__", ".idea", ".vscode",
    "build", "cmake-build-debug", "dist", "target", "vendor", "third_party",
    ".codebuddy", ".claude", "venv", ".venv", "out", "bin", "obj",
}


def _walk(root, max_files=20000):
    collected = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in IGNORED_DIRS and not d.startswith(".")]
        for name in filenames:
            collected.append(os.path.join(dirpath, name))
            if len(collected) >= max_files:
                return collected
    return collected


def _hot_files(root, limit=15):
    """用 git 历史找热点文件 —— 改得最频繁的地方通常就是需求最常落地的地方。"""
    code, out, _ = core.run_cmd(
        "git log --since=12.month --name-only --pretty=format: -- . | grep -v '^$'",
        cwd=root, timeout=60)
    if code != 0 or not out:
        return []
    counter = Counter(line.strip() for line in out.splitlines() if line.strip())
    hot = []
    for path, count in counter.most_common(limit * 3):
        if any(part in IGNORED_DIRS for part in path.split(os.sep)):
            continue
        if os.path.isfile(os.path.join(root, path)):
            hot.append((path, count))
        if len(hot) >= limit:
            break
    return hot


def scan(root=None, top_modules=15):
    root = root or core.project_root()
    files = _walk(root)

    languages = Counter()
    build_systems = set()
    modules = Counter()
    entrypoints = []

    for path in files:
        rel = os.path.relpath(path, root)
        base = os.path.basename(path)
        ext = os.path.splitext(base)[1].lower()

        if base in BUILD_MARKERS:
            build_systems.add(BUILD_MARKERS[base])
        if ext in LANGUAGE_BY_EXT:
            languages[LANGUAGE_BY_EXT[ext]] += 1
            top = rel.split(os.sep)[0]
            if top and not top.startswith("."):
                modules[top] += 1
        if base in ("main.cpp", "main.cc", "main.c", "main.go", "main.py",
                    "__main__.py", "index.ts", "index.js", "Main.java"):
            entrypoints.append(rel)

    hot = _hot_files(root)
    code, branch, _ = core.run_cmd("git branch --show-current", cwd=root, timeout=15)
    branch = branch if code == 0 else "(非 git 仓库)"

    return {
        "root": root,
        "branch": branch,
        "file_count": len(files),
        "languages": languages.most_common(10),
        "build_systems": sorted(build_systems),
        "modules": modules.most_common(top_modules),
        "entrypoints": entrypoints[:10],
        "hot_files": hot,
    }


def render_markdown(data):
    lines = [
        "# 仓库地图",
        "",
        "> 由 `devflow repo-map` 自动生成，供流水线各阶段精准加载。",
        "> 它回答的是「这个仓库长什么样、改动通常落在哪」，",
        "> 不替代 architect 阶段针对具体需求的代码探索。",
        "",
        "| 项 | 值 |",
        "|---|---|",
        "| 根目录 | `%s` |" % data["root"],
        "| 当前分支 | `%s` |" % data["branch"],
        "| 扫描文件数 | %d |" % data["file_count"],
        "| 构建体系 | %s |" % (", ".join(data["build_systems"]) or "未识别"),
        "",
        "## 语言分布",
        "",
    ]
    if data["languages"]:
        lines += ["| 语言 | 文件数 |", "|---|---|"]
        lines += ["| %s | %d |" % (lang, count) for lang, count in data["languages"]]
    else:
        lines.append("未识别到源码文件。")

    lines += ["", "## 顶层模块", "",
              "按源码文件数排序，是需求落地时最可能涉及的目录。", ""]
    if data["modules"]:
        lines += ["| 模块 | 源码文件数 |", "|---|---|"]
        lines += ["| `%s` | %d |" % (name, count) for name, count in data["modules"]]
    else:
        lines.append("未识别到模块目录。")

    lines += ["", "## 入口点", ""]
    lines += (["- `%s`" % e for e in data["entrypoints"]] if data["entrypoints"]
              else ["未识别到常见入口文件。"])

    lines += ["", "## 热点文件（近 12 个月改动最频繁）", "",
              "热点文件往往是业务核心，改动前应重点评估影响范围。", ""]
    if data["hot_files"]:
        lines += ["| 文件 | 提交次数 |", "|---|---|"]
        lines += ["| `%s` | %d |" % (path, count) for path, count in data["hot_files"]]
    else:
        lines.append("无 git 历史或仓库过新，暂无热点统计。")

    lines += ["", "---", "", "生成时间：%s" % core.now_iso(), ""]
    return "\n".join(lines)


def generate(root=None):
    data = scan(root)
    core.write_text(core.REPO_MAP_FILE, render_markdown(data))
    return {
        "action": "repo-map",
        "output": core.REPO_MAP_FILE,
        "file_count": data["file_count"],
        "languages": [l for l, _ in data["languages"]],
        "build_systems": data["build_systems"],
        "modules": [m for m, _ in data["modules"]],
        "hot_files": [h for h, _ in data["hot_files"]][:10],
    }
