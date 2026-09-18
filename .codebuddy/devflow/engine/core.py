# -*- coding: utf-8 -*-
"""devflow 引擎 L1 — 领域无关的基础设施层。

本模块只做四件事：定位路径、读写文件、加载配置、执行命令。
它不认识任何具体技术栈，所有与语言/构建/发布相关的知识都来自 profile。

兼容性：Python 3.6+ / PyYAML 3.x（不使用 f-string 以外的新语法，
不使用 dataclasses、subprocess.run(capture_output=) 等 3.7+ 特性）。
"""

import json
import os
import re
import subprocess
import sys
from datetime import datetime

try:
    import yaml
except ImportError:
    sys.stderr.write("ERROR: 缺少 PyYAML，请先执行 pip3 install pyyaml\n")
    sys.exit(2)


# ============================================================
# 路径定位
# ============================================================

ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))
DEVFLOW_DIR = os.path.dirname(ENGINE_DIR)              # .codebuddy/devflow
CODEBUDDY_DIR = os.path.dirname(DEVFLOW_DIR)           # .codebuddy
CONFIG_DIR = os.path.join(DEVFLOW_DIR, "config")
PROFILES_DIR = os.path.join(CONFIG_DIR, "profiles")
PROMPTS_DIR = os.path.join(DEVFLOW_DIR, "prompts")
LEARNINGS_DIR = os.path.join(DEVFLOW_DIR, "learnings")
EVENTS_DIR = os.path.join(LEARNINGS_DIR, "events")
RUNS_DIR = os.path.join(DEVFLOW_DIR, "runs")
WORKFLOW_FILE = os.path.join(CONFIG_DIR, "workflow.yaml")
ACTIVE_LEARNINGS_FILE = os.path.join(LEARNINGS_DIR, "active.yaml")
REPO_MAP_FILE = os.path.join(DEVFLOW_DIR, "repo-map.md")


def project_root():
    """宿主仓库根目录。

    允许用 DEVFLOW_PROJECT_ROOT 覆盖，便于测试与 demo 在临时目录中运行。
    """
    override = os.environ.get("DEVFLOW_PROJECT_ROOT")
    if override:
        return os.path.abspath(override)
    return os.path.dirname(CODEBUDDY_DIR)


def run_dir(run_id):
    return os.path.join(RUNS_DIR, run_id)


def ensure_dir(path):
    if path and not os.path.isdir(path):
        os.makedirs(path)
    return path


# ============================================================
# 时间
# ============================================================

def now_iso():
    return datetime.now().replace(microsecond=0).isoformat()


def today():
    return datetime.now().strftime("%Y-%m-%d")


def elapsed_ms(start_iso):
    if not start_iso:
        return None
    try:
        start = datetime.strptime(start_iso, "%Y-%m-%dT%H:%M:%S")
    except (ValueError, TypeError):
        return None
    delta = datetime.now().replace(microsecond=0) - start
    return int(delta.total_seconds() * 1000)


# ============================================================
# 读写
# ============================================================

def read_text(path, default=""):
    if not os.path.isfile(path):
        return default
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        return handle.read()


def write_text(path, content):
    ensure_dir(os.path.dirname(path))
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)


def read_json(path, default=None):
    if not os.path.isfile(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except ValueError:
        return default


def write_json(path, data):
    ensure_dir(os.path.dirname(path))
    payload = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=False)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(payload + "\n")


def read_yaml(path, default=None):
    if not os.path.isfile(path):
        return default if default is not None else {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle.read())
        return data if data is not None else (default if default is not None else {})
    except yaml.YAMLError as exc:
        raise ConfigError("YAML 解析失败 %s: %s" % (path, exc))


def emit(payload, exit_code=0):
    """所有 CLI 子命令的统一输出口：一律 JSON，便于 Agent 解析。"""
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return exit_code


class ConfigError(Exception):
    pass


# ============================================================
# 命令执行
# ============================================================

def run_cmd(command, cwd=None, timeout=1800):
    """执行 shell 命令，返回 (returncode, stdout, stderr)。

    使用 3.6 兼容写法（stdout=PIPE + universal_newlines）。
    """
    if not command:
        return (0, "", "")
    try:
        proc = subprocess.Popen(
            command,
            shell=True,
            cwd=cwd or project_root(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
        )
        out, err = proc.communicate(timeout=timeout)
        return (proc.returncode, (out or "").strip(), (err or "").strip())
    except subprocess.TimeoutExpired:
        proc.kill()
        return (124, "", "命令超时（%ss）: %s" % (timeout, command))
    except OSError as exc:
        return (127, "", str(exc))


# ============================================================
# 配置加载：workflow.yaml + profile
# ============================================================

_VAR_PATTERN = re.compile(r"\{\{\s*([a-zA-Z0-9_.]+)\s*\}\}")


def render(template, variables):
    """渲染 {{ var }} 占位符，支持 a.b.c 点号取值。未知变量原样保留。"""
    if not isinstance(template, str):
        return template

    def _lookup(match):
        key = match.group(1)
        node = variables
        for part in key.split("."):
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                return match.group(0)
        return str(node)

    return _VAR_PATTERN.sub(_lookup, template)


def render_deep(node, variables):
    if isinstance(node, dict):
        return dict((k, render_deep(v, variables)) for k, v in node.items())
    if isinstance(node, list):
        return [render_deep(v, variables) for v in node]
    return render(node, variables)


def load_workflow():
    workflow = read_yaml(WORKFLOW_FILE)
    if not workflow.get("stages"):
        raise ConfigError("workflow.yaml 缺少 stages 定义")
    return workflow


def load_profile(name=None):
    """加载 profile。未指定时读 workflow.yaml 的 defaults.profile。"""
    if not name:
        name = load_workflow().get("defaults", {}).get("profile", "default")
    path = os.path.join(PROFILES_DIR, name + ".yaml")
    if not os.path.isfile(path):
        available = sorted(
            f[:-5] for f in os.listdir(PROFILES_DIR) if f.endswith(".yaml") and not f.startswith("_")
        )
        raise ConfigError("找不到 profile '%s'，可用：%s" % (name, ", ".join(available)))
    profile = read_yaml(path)
    profile["_name"] = name
    profile["_path"] = path
    return profile


def stage_defs(workflow=None):
    return (workflow or load_workflow()).get("stages", [])


def find_stage(stage_id, workflow=None):
    for stage in stage_defs(workflow):
        if stage.get("id") == stage_id:
            return stage
    return None


def skipped_stages(run_type, workflow=None):
    workflow = workflow or load_workflow()
    types = workflow.get("run_types", {})
    return list(types.get(run_type, {}).get("skip", []))


def active_stages(run_type, workflow=None):
    workflow = workflow or load_workflow()
    skip = skipped_stages(run_type, workflow)
    return [s for s in stage_defs(workflow) if s.get("id") not in skip]


def profile_command(profile, key, variables=None):
    """从 profile.commands 取一条命令并完成变量渲染。缺失返回空串。"""
    command = (profile.get("commands") or {}).get(key, "")
    if not command:
        return ""
    return render(command, variables or {})
