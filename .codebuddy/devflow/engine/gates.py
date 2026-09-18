# -*- coding: utf-8 -*-
"""门禁引擎。

设计立场（相对早期原型的两点改进）：

1. **门禁是配置，不是代码。** 早期原型把 23 项检查硬编码成 23 个 Python
   函数，加一个阶段就要改引擎。这里只实现约 10 个通用校验原语，具体检查
   什么写在 workflow.yaml 里，换技术栈不用碰引擎。

2. **门禁独立复算，不信 Agent 自述。** 早期原型校验编译是否成功的方式是
   在 markdown 里 grep "BUILD SUCCESSFUL" —— 而这行字恰恰是 LLM 自己写的。
   command_ok 原语会真正把 profile 里的构建/测试命令重跑一遍，用退出码说话。
"""

import os
import re

from . import core

PASS = "PASS"
FAIL = "FAIL"
WARN = "WARN"


class GateResult(object):
    def __init__(self, check, status, evidence="", message="", severity="block"):
        self.check = check
        self.status = status
        self.evidence = evidence
        self.message = message
        self.severity = severity

    @property
    def blocking(self):
        return self.status == FAIL and self.severity == "block"

    def to_dict(self):
        return {
            "check": self.check,
            "status": self.status,
            "severity": self.severity,
            "evidence": self.evidence,
            "message": self.message,
        }


# ============================================================
# 取值辅助
# ============================================================

def _resolve(base_dir, filename):
    return filename if os.path.isabs(filename) else os.path.join(base_dir, filename)


def _files_of(spec):
    files = spec.get("files")
    if files:
        return list(files)
    single = spec.get("file")
    return [single] if single else []


def _yaml_path(data, path):
    """按 a.b.c 取值，路径不存在返回 None。"""
    node = data
    for part in (path or "").split("."):
        if not part:
            continue
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            return None
    return node


def _as_file_list(items):
    """依赖地图条目允许写成纯字符串或 {file: ...} 字典。"""
    result = []
    for item in items or []:
        if isinstance(item, dict):
            value = item.get("file") or item.get("path") or item.get("name")
            if value:
                result.append(value)
        elif isinstance(item, str):
            result.append(item)
    return result


# ============================================================
# 校验原语
# ============================================================

def _check_file_exists(spec, ctx):
    results = []
    for name in _files_of(spec):
        path = _resolve(ctx["artifacts_dir"], name)
        ok = os.path.isfile(path)
        results.append(GateResult(
            "file_exists:%s" % name,
            PASS if ok else FAIL,
            path if ok else "未找到 %s" % path,
            spec.get("message", ""),
            spec.get("severity", "block"),
        ))
    return results


def _check_file_nonempty(spec, ctx):
    min_chars = int(spec.get("min_chars", 50))
    results = []
    for name in _files_of(spec):
        path = _resolve(ctx["artifacts_dir"], name)
        content = core.read_text(path).strip()
        ok = len(content) >= min_chars
        results.append(GateResult(
            "file_nonempty:%s" % name,
            PASS if ok else FAIL,
            "%d 字符（要求 >= %d）" % (len(content), min_chars),
            spec.get("message", ""),
            spec.get("severity", "block"),
        ))
    return results


def _check_sections_present(spec, ctx):
    name = spec.get("file")
    path = _resolve(ctx["artifacts_dir"], name)
    content = core.read_text(path)
    all_of = spec.get("all_of") or []
    any_of = spec.get("any_of") or []
    missing = [s for s in all_of if s not in content]
    any_hit = [s for s in any_of if s in content]
    ok = not missing and (not any_of or len(any_hit) >= int(spec.get("min_any", 1)))
    evidence_parts = []
    if missing:
        evidence_parts.append("缺少必需章节 %s" % missing)
    if any_of and len(any_hit) < int(spec.get("min_any", 1)):
        evidence_parts.append("候选章节 %s 命中不足" % any_of)
    if ok:
        evidence_parts.append("章节齐备")
    return [GateResult(
        "sections_present:%s" % name,
        PASS if ok else FAIL,
        "；".join(evidence_parts),
        spec.get("message", ""),
        spec.get("severity", "block"),
    )]


def _check_regex_present(spec, ctx):
    name = spec.get("file")
    path = _resolve(ctx["artifacts_dir"], name)
    content = core.read_text(path)
    pattern = re.compile(spec["pattern"], re.IGNORECASE if spec.get("ignore_case") else 0)
    hits = pattern.findall(content)
    unique = len(set(hits))
    min_count = int(spec.get("min_count", 1))
    ok = unique >= min_count
    return [GateResult(
        "regex_present:%s:%s" % (name, spec.get("label", spec["pattern"])),
        PASS if ok else FAIL,
        "匹配 %d 处（去重 %d，要求 >= %d）" % (len(hits), unique, min_count),
        spec.get("message", ""),
        spec.get("severity", "block"),
    )]


def _check_regex_absent(spec, ctx):
    name = spec.get("file")
    path = _resolve(ctx["artifacts_dir"], name)
    content = core.read_text(path)
    pattern = re.compile(spec["pattern"], re.IGNORECASE if spec.get("ignore_case") else 0)
    hits = pattern.findall(content)
    ok = not hits
    return [GateResult(
        "regex_absent:%s:%s" % (name, spec.get("label", spec["pattern"])),
        PASS if ok else FAIL,
        "未出现禁用模式" if ok else "出现 %d 处禁用内容：%s" % (len(hits), sorted(set(hits))[:5]),
        spec.get("message", ""),
        spec.get("severity", "block"),
    )]


def _check_yaml_valid(spec, ctx):
    results = []
    for name in _files_of(spec):
        path = _resolve(ctx["artifacts_dir"], name)
        try:
            data = core.read_yaml(path, default=None)
            ok = isinstance(data, dict) and bool(data)
            evidence = "解析成功，顶层 %d 个键" % len(data) if ok else "内容为空或顶层不是映射"
        except core.ConfigError as exc:
            ok = False
            evidence = str(exc)
        results.append(GateResult(
            "yaml_valid:%s" % name,
            PASS if ok else FAIL,
            evidence,
            spec.get("message", ""),
            spec.get("severity", "block"),
        ))
    return results


def _check_yaml_path_empty(spec, ctx):
    """用于「uncovered_requirements 必须为空」这类反向约束。"""
    name = spec.get("file")
    path = _resolve(ctx["artifacts_dir"], name)
    data = core.read_yaml(path, default={}) or {}
    value = _yaml_path(data, spec["path"])
    ok = not value
    return [GateResult(
        "yaml_path_empty:%s:%s" % (name, spec["path"]),
        PASS if ok else FAIL,
        "为空" if ok else "非空：%s" % value,
        spec.get("message", ""),
        spec.get("severity", "block"),
    )]


def _check_yaml_path_nonempty(spec, ctx):
    name = spec.get("file")
    path = _resolve(ctx["artifacts_dir"], name)
    data = core.read_yaml(path, default={}) or {}
    value = _yaml_path(data, spec["path"])
    ok = bool(value)
    size = len(value) if hasattr(value, "__len__") else 1
    return [GateResult(
        "yaml_path_nonempty:%s:%s" % (name, spec["path"]),
        PASS if ok else FAIL,
        "含 %d 项" % size if ok else "缺失或为空",
        spec.get("message", ""),
        spec.get("severity", "block"),
    )]


def _check_yaml_items_field(spec, ctx):
    """校验列表中每一项的某字段取值，例如所有 task 的 status 必须是 completed。"""
    name = spec.get("file")
    path = _resolve(ctx["artifacts_dir"], name)
    data = core.read_yaml(path, default={}) or {}
    items = _yaml_path(data, spec["path"]) or []
    field = spec["field"]
    expected = spec.get("equals")
    exclude = spec.get("exclude_types") or []
    bad = []
    checked = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get("type") in exclude:
            continue
        checked += 1
        value = item.get(field)
        if expected is not None:
            if value != expected:
                bad.append("%s(%s=%s)" % (item.get("id", "?"), field, value))
        elif not value:
            bad.append("%s(缺 %s)" % (item.get("id", "?"), field))
    ok = not bad
    return [GateResult(
        "yaml_items_field:%s.%s" % (spec["path"], field),
        PASS if ok else FAIL,
        "%d 项全部符合" % checked if ok else "不符合的项：%s" % bad,
        spec.get("message", ""),
        spec.get("severity", "block"),
    )]


def _check_command_ok(spec, ctx):
    """真正重跑一遍 profile 里的命令，用退出码判定，而不是相信产物里的自述。"""
    key = spec.get("profile_command")
    command = core.profile_command(ctx["profile"], key, ctx.get("variables"))
    if not command:
        # profile 未配置该命令：按 on_missing 决定降级还是阻断
        on_missing = spec.get("on_missing", "warn")
        return [GateResult(
            "command_ok:%s" % key,
            WARN if on_missing == "warn" else FAIL,
            "profile '%s' 未配置 commands.%s，已跳过独立复算" % (ctx["profile"].get("_name"), key),
            spec.get("message", ""),
            "warn" if on_missing == "warn" else "block",
        )]
    code, out, err = core.run_cmd(command, timeout=int(spec.get("timeout", 1800)))
    ok = code == 0
    pattern = spec.get("expect_pattern")
    if ok and pattern:
        ok = bool(re.search(pattern, out + "\n" + err, re.IGNORECASE))
    tail = (out or err or "").splitlines()
    tail = " / ".join(tail[-3:])[:300]
    return [GateResult(
        "command_ok:%s" % key,
        PASS if ok else FAIL,
        "`%s` 退出码 %d%s" % (command, code, ("；输出尾部：" + tail) if tail else ""),
        spec.get("message", ""),
        spec.get("severity", "block"),
    )]


PRIMITIVES = {
    "file_exists": _check_file_exists,
    "file_nonempty": _check_file_nonempty,
    "sections_present": _check_sections_present,
    "regex_present": _check_regex_present,
    "regex_absent": _check_regex_absent,
    "yaml_valid": _check_yaml_valid,
    "yaml_path_empty": _check_yaml_path_empty,
    "yaml_path_nonempty": _check_yaml_path_nonempty,
    "yaml_items_field": _check_yaml_items_field,
    "command_ok": _check_command_ok,
}


# ============================================================
# 门禁执行
# ============================================================

def run_gates(stage_def, artifacts_dir_path, profile, variables=None):
    ctx = {
        "artifacts_dir": artifacts_dir_path,
        "profile": profile,
        "variables": variables or {},
    }
    results = []
    for spec in stage_def.get("gates", []) or []:
        name = spec.get("check")
        handler = PRIMITIVES.get(name)
        if not handler:
            results.append(GateResult(
                "unknown:%s" % name, FAIL,
                "workflow.yaml 引用了未知校验原语 '%s'" % name,
                "可用原语：%s" % ", ".join(sorted(PRIMITIVES)),
            ))
            continue
        try:
            results.extend(handler(spec, ctx))
        except (KeyError, re.error, OSError) as exc:
            results.append(GateResult(
                "error:%s" % name, FAIL,
                "校验项配置错误：%s" % exc,
                spec.get("message", ""),
            ))
    return results


def summarize(results, stage_id):
    blocking = [r for r in results if r.blocking]
    warned = [r for r in results if r.status == WARN]
    passed = [r for r in results if r.status == PASS]
    total = len(results)
    score = int(round(len(passed) * 100.0 / total)) if total else 100
    return {
        "stage": stage_id,
        "overall": "FAIL" if blocking else "PASS",
        "score": score,
        "total": total,
        "passed": len(passed),
        "failed": len([r for r in results if r.status == FAIL]),
        "warned": len(warned),
        "checks": [r.to_dict() for r in results],
        "blocking": [r.to_dict() for r in blocking],
    }


# ============================================================
# 计划结构校验（原 plan-reviewer 角色的 9 项检查）
# ============================================================
# 早期原型用一个 LLM subagent 做这些检查。但它们全是确定性的图/集合运算，
# 交给脚本既快又稳，还不会因为模型波动而漏判。这是「能脚本化的绝不交给 LLM」
# 原则最直接的体现，也是 9 个角色收敛到 7 个的依据。

def _task_files(task):
    return _as_file_list(task.get("files_to_modify"))


def _must_notify_files(task):
    contract = task.get("completeness_contract") or {}
    return _as_file_list(contract.get("must_notify"))


def run_plan_check(artifacts_dir_path, tasks_file="tasks.yaml",
                   depmap_file="dependency-map.yaml", max_files_per_task=5):
    tasks_path = _resolve(artifacts_dir_path, tasks_file)
    depmap_path = _resolve(artifacts_dir_path, depmap_file)
    results = []

    def add(check, ok, evidence, message="", severity="block"):
        results.append(GateResult(check, PASS if ok else FAIL, evidence, message, severity))

    if not os.path.isfile(tasks_path):
        add("CHECK-0:tasks.yaml 存在", False, "未找到 %s" % tasks_path)
        return summarize(results, "plan-check")

    try:
        tasks_doc = core.read_yaml(tasks_path, default={}) or {}
    except core.ConfigError as exc:
        add("CHECK-0:tasks.yaml 可解析", False, str(exc))
        return summarize(results, "plan-check")

    depmap = {}
    if os.path.isfile(depmap_path):
        try:
            depmap = core.read_yaml(depmap_path, default={}) or {}
        except core.ConfigError as exc:
            add("CHECK-0:dependency-map.yaml 可解析", False, str(exc))
    if "dependency_map" in depmap:
        depmap = depmap["dependency_map"]

    tasks = [t for t in (tasks_doc.get("tasks") or []) if isinstance(t, dict)]
    requirements = tasks_doc.get("requirements") or []
    coverage = tasks_doc.get("coverage_matrix") or {}
    req_ids = []
    for item in requirements:
        if isinstance(item, dict):
            req_ids.append(item.get("id"))
        elif isinstance(item, str):
            req_ids.append(item)
    req_ids = [r for r in req_ids if r]
    task_ids = [t.get("id") for t in tasks if t.get("id")]
    req_to_task = coverage.get("requirement_to_task") or {}

    # CHECK-1 需求覆盖完整性
    uncovered = [r for r in req_ids if not req_to_task.get(r)]
    declared_uncovered = coverage.get("uncovered_requirements") or []
    add("CHECK-1:需求覆盖完整性", not uncovered and not declared_uncovered,
        "%d 个需求全部有任务承接" % len(req_ids) if not uncovered and not declared_uncovered
        else "未覆盖需求 %s（声明的未覆盖项 %s）" % (uncovered, declared_uncovered),
        "每个 R 编号必须至少落到一个 task，否则需求会在实现阶段静默丢失")

    # CHECK-2 孤立任务
    task_to_req = coverage.get("task_to_requirement") or {}
    orphans = [t.get("id") for t in tasks
               if t.get("type") != "quality_gate" and not task_to_req.get(t.get("id"))]
    declared_orphans = coverage.get("orphan_tasks") or []
    add("CHECK-2:无孤立任务", not orphans and not declared_orphans,
        "全部任务可追溯到需求" if not orphans and not declared_orphans
        else "孤立任务 %s（声明的孤立项 %s）" % (orphans, declared_orphans),
        "任务追溯不到需求，说明要么需求漏写，要么任务是多余的镀金")

    # CHECK-3 依赖地图核心层覆盖
    core_files = _as_file_list(depmap.get("core"))
    covered_files = set()
    for task in tasks:
        covered_files.update(_task_files(task))
    missing_core = [f for f in core_files if f not in covered_files]
    add("CHECK-3:核心层文件全覆盖", not missing_core,
        "%d 个核心文件均已分配任务" % len(core_files) if not missing_core
        else "核心文件无任务认领：%s" % missing_core,
        "dependency-map 里标为 core 的文件是需求必改项，漏掉即实现不完整")

    # CHECK-4 依赖地图关联层覆盖
    assoc_files = _as_file_list(depmap.get("associated"))
    notified = set()
    for task in tasks:
        notified.update(_must_notify_files(task))
    missing_assoc = [f for f in assoc_files
                     if f not in covered_files and f not in notified]
    add("CHECK-4:关联层文件已知会", not missing_assoc,
        "%d 个关联文件均已覆盖或登记 must_notify" % len(assoc_files) if not missing_assoc
        else "关联文件既未修改也未登记 must_notify：%s" % missing_assoc,
        "关联层是调用方，不改也要在 completeness_contract.must_notify 里登记以便回归检查")

    # CHECK-5 依赖关系无环
    graph = dict((t.get("id"), [d for d in (t.get("depends_on") or [])]) for t in tasks)
    cycle = _find_cycle(graph)
    add("CHECK-5:依赖图无环", cycle is None,
        "DAG 合法" if cycle is None else "检测到环：%s" % " -> ".join(cycle),
        "存在环时 developer 无法确定执行顺序，会死锁在拓扑排序上")

    # CHECK-6 依赖指向存在的任务
    dangling = []
    for tid, deps in graph.items():
        for dep in deps:
            if dep not in task_ids:
                dangling.append("%s -> %s" % (tid, dep))
    add("CHECK-6:依赖指向有效任务", not dangling,
        "全部 depends_on 有效" if not dangling else "悬空依赖：%s" % dangling)

    # CHECK-7 任务粒度
    oversized = ["%s(%d 文件)" % (t.get("id"), len(_task_files(t)))
                 for t in tasks if len(_task_files(t)) > max_files_per_task]
    add("CHECK-7:任务粒度可控", not oversized,
        "全部任务改动文件数 <= %d" % max_files_per_task if not oversized
        else "粒度过大需继续拆分：%s" % oversized,
        "单任务改动过多文件会让编译验证失去定位能力，失败时无法二分", "warn")

    # CHECK-8 验收条件完整
    no_acceptance = [t.get("id") for t in tasks
                     if t.get("type") != "quality_gate" and not t.get("acceptance")]
    add("CHECK-8:验收条件完整", not no_acceptance,
        "全部任务有 acceptance" if not no_acceptance
        else "缺少 acceptance 的任务：%s" % no_acceptance,
        "没有验收条件，developer 无法自证任务完成")

    # CHECK-9 质量门禁任务存在
    qg = [t.get("id") for t in tasks if t.get("type") == "quality_gate"]
    add("CHECK-9:含质量门禁任务", bool(qg),
        "质量门禁任务：%s" % qg if qg else "tasks.yaml 末尾缺少 type: quality_gate 任务",
        "质量门禁任务负责收尾扫描（TODO 残留、死代码、整体构建），不能省")

    return summarize(results, "plan-check")


def _find_cycle(graph):
    """返回任意一条环路径，无环返回 None。"""
    WHITE, GREY, BLACK = 0, 1, 2
    color = dict((node, WHITE) for node in graph)
    stack = []

    def visit(node):
        color[node] = GREY
        stack.append(node)
        for nxt in graph.get(node) or []:
            if nxt not in color:
                continue
            if color[nxt] == GREY:
                return stack[stack.index(nxt):] + [nxt]
            if color[nxt] == WHITE:
                found = visit(nxt)
                if found:
                    return found
        color[node] = BLACK
        stack.pop()
        return None

    for node in list(graph):
        if color.get(node) == WHITE:
            found = visit(node)
            if found:
                return found
    return None
