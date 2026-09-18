# -*- coding: utf-8 -*-
"""devflow 命令行入口。

所有子命令统一输出 JSON，便于 Leader Agent 直接解析；退出码承载判定结果：

    0 = 成功 / 门禁通过
    1 = 门禁不通过（业务失败，Leader 应据此打回重做）
    2 = 执行错误（配置缺失、参数错误等，Leader 应据此报错而非重试）
"""

import argparse
import os
import sys

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from engine import core, gates, learning, repomap, runner
    from engine.state import RunState
else:
    from . import core, gates, learning, repomap, runner
    from .state import RunState

VERSION = "1.0.0"


# ============================================================
# 子命令
# ============================================================

def cmd_init(args):
    workflow = core.load_workflow()
    profile = core.load_profile(args.profile)
    run_id = args.run_id or _generate_run_id()
    path = os.path.join(core.RUNS_DIR, run_id, "run-state.json")
    if os.path.isfile(path) and not args.force:
        return core.emit({"error": "运行 '%s' 已存在，加 --force 覆盖或换一个 --run-id" % run_id}, 2)

    state = RunState.create(run_id, args.title, args.type, profile["_name"], workflow)
    if args.requirement:
        state.update_context(requirement_summary=args.requirement)

    active = core.active_stages(args.type, workflow)
    return core.emit({
        "action": "init",
        "run_id": run_id,
        "title": args.title,
        "type": args.type,
        "profile": profile["_name"],
        "artifacts_dir": state.artifacts_dir,
        "skipped_stages": core.skipped_stages(args.type, workflow),
        "active_stages": [{"id": s["id"], "name": s.get("name"), "role": s.get("role")} for s in active],
        "first_stage": active[0]["id"] if active else None,
        "repo_map_ready": os.path.isfile(core.REPO_MAP_FILE),
        "next": "devflow next --run-id %s" % run_id,
    })


def _generate_run_id():
    from datetime import datetime
    stamp = datetime.now().strftime("%Y%m%d")
    index = 1
    while os.path.isdir(os.path.join(core.RUNS_DIR, "REQ-%s-%02d" % (stamp, index))):
        index += 1
    return "REQ-%s-%02d" % (stamp, index)


def cmd_next(args):
    workflow = core.load_workflow()
    state = RunState.load_or_latest(args.run_id)
    profile = core.load_profile(state.profile_name)
    payload = runner.next_prompt(state, workflow, profile, record=not args.peek)
    if payload.get("stage") and not args.peek:
        state.start_stage(payload["stage"])
    return core.emit(payload)


def cmd_gate(args):
    workflow = core.load_workflow()
    state = RunState.load_or_latest(args.run_id)
    profile = core.load_profile(state.profile_name)
    summary = runner.run_stage_gate(state, args.stage, workflow, profile)
    code = 0 if summary["overall"] == "PASS" else 1
    if code:
        for item in summary["blocking"]:
            sys.stderr.write("FAIL %s — %s\n" % (item["check"], item["evidence"]))
    return core.emit(summary, code)


def cmd_plan_check(args):
    state = RunState.load_or_latest(args.run_id)
    summary = gates.run_plan_check(state.artifacts_dir, max_files_per_task=args.max_files)
    if summary["overall"] != "PASS":
        learning.record_gate_failures(state.run_id, "plan-check", summary)
        for item in summary["blocking"]:
            sys.stderr.write("FAIL %s — %s\n" % (item["check"], item["evidence"]))
    return core.emit(summary, 0 if summary["overall"] == "PASS" else 1)


def cmd_stage_update(args):
    workflow = core.load_workflow()
    state = RunState.load_or_latest(args.run_id)
    summary = runner.advance(state, args.stage, args.status, workflow,
                             summary_text=args.summary, score=args.score)
    if args.decision or args.key_files:
        state.update_context(
            technical_decisions=[d for d in (args.decision or [])] or None,
            key_files=[f for f in (args.key_files or [])] or None,
        )
    return core.emit({"action": "stage-update", "stage": args.stage,
                      "status": args.status, "run": summary})


def cmd_status(args):
    workflow = core.load_workflow()
    state = RunState.load_or_latest(args.run_id)
    payload = state.summary(workflow)
    payload["action"] = "status"
    payload["learning_library"] = learning.stats_overview()
    return core.emit(payload)


def cmd_list(args):
    return core.emit({"action": "list", "runs": RunState.list_runs()})


def cmd_resume(args):
    workflow = core.load_workflow()
    state = RunState.load_or_latest(args.run_id)
    profile = core.load_profile(state.profile_name)
    return core.emit(runner.resume(state, workflow, profile))


def cmd_inspect(args):
    workflow = core.load_workflow()
    state = RunState.load_or_latest(args.run_id)
    stage = args.stage or state.data.get("current_stage")
    if not stage:
        return core.emit({"error": "没有进行中的阶段，请用 --stage 指定"}, 2)
    payload = runner.inspect_stage_artifacts(state, stage, workflow)
    learning.record_event(
        state.run_id, stage, "silent_recovery",
        "inspect %s → %s（present=%s missing=%s）" % (
            stage, payload.get("verdict"),
            ",".join(payload.get("present") or []) or "-",
            ",".join(payload.get("missing") or []) or "-"),
        tags=["inspect", stage],
        signature="silent_recovery:%s:%s" % (stage, payload.get("verdict")))
    return core.emit(payload)


def cmd_rollback(args):
    workflow = core.load_workflow()
    state = RunState.load_or_latest(args.run_id)
    cleared = state.rollback(args.to, workflow, args.reason)
    learning.record_event(state.run_id, args.to, "rollback",
                          "回退到 %s：%s" % (args.to, args.reason or "未说明原因"),
                          tags=["rollback", args.to],
                          signature="rollback:%s" % args.to)
    return core.emit({"action": "rollback", "target": args.to,
                      "cleared_stages": cleared, "run": state.summary(workflow)})


def cmd_learn(args):
    state = RunState.load_or_latest(args.run_id)
    event = learning.record_event(
        state.run_id, args.stage, args.type, args.detail, args.fix,
        [t.strip() for t in (args.tags or "").split(",") if t.strip()],
        args.signature)
    return core.emit({"action": "learn", "event": event})


def cmd_distill(args):
    return core.emit(learning.distill(apply_changes=not args.dry_run))


def cmd_learnings(args):
    tags = [t.strip() for t in (args.tags or "").split(",") if t.strip()]
    matched = learning.query(stage=args.stage, tags=tags, limit=args.limit)
    return core.emit({
        "action": "learnings",
        "stage": args.stage,
        "matched": len(matched),
        "overview": learning.stats_overview(),
        "entries": [{
            "id": e["id"], "priority": e.get("priority"), "status": e.get("status"),
            "stage": e.get("stage"), "pattern": e.get("pattern"), "fix": e.get("fix"),
            "stats": e.get("stats"),
        } for e in matched],
        "prompt_block": learning.format_for_prompt(matched),
    })


def cmd_reflect(args):
    state = RunState.load_or_latest(args.run_id)
    distilled = learning.distill(apply_changes=True)
    payload = learning.reflect(state.run_id)
    payload["action"] = "reflect"
    payload["distilled"] = distilled
    return core.emit(payload)


def cmd_repo_map(args):
    return core.emit(repomap.generate(args.root))


def cmd_profiles(args):
    available = []
    for name in sorted(os.listdir(core.PROFILES_DIR)):
        if not name.endswith(".yaml") or name.startswith("_"):
            continue
        profile = core.read_yaml(os.path.join(core.PROFILES_DIR, name), default={}) or {}
        available.append({
            "name": name[:-5],
            "description": profile.get("description", ""),
            "languages": profile.get("languages", []),
            "commands": sorted(k for k, v in (profile.get("commands") or {}).items() if v),
        })
    return core.emit({
        "action": "profiles",
        "default": core.load_workflow().get("defaults", {}).get("profile"),
        "profiles": available,
    })


def cmd_doctor(args):
    """环境自检：跑流水线之前先确认脚手架是否完整、profile 命令是否可用。"""
    workflow = core.load_workflow()
    profile = core.load_profile(args.profile)
    checks = []

    def add(name, ok, detail):
        checks.append({"check": name, "status": "PASS" if ok else "FAIL", "detail": detail})

    add("workflow.yaml 可加载", True, "%d 个阶段" % len(core.stage_defs(workflow)))
    add("profile 可加载", True, "%s（%s）" % (profile["_name"], profile.get("description", "")))

    missing_prompts = []
    for stage in core.stage_defs(workflow):
        name = stage.get("prompt") or ("%s.md" % stage["id"].lower())
        if not os.path.isfile(os.path.join(core.PROMPTS_DIR, name)):
            missing_prompts.append(name)
    add("阶段 prompt 模板齐备", not missing_prompts,
        "全部就绪" if not missing_prompts else "缺失：%s" % missing_prompts)

    agents_dir = os.path.join(core.CODEBUDDY_DIR, "agents")
    # leader 不属于任何阶段（它是编排者），所以要显式加进来一起校验
    roles = sorted(set(["leader"] + [s.get("role") for s in core.stage_defs(workflow) if s.get("role")]))
    missing_agents = [r for r in roles if not os.path.isfile(os.path.join(agents_dir, r + ".md"))]
    add("角色定义齐备", not missing_agents,
        "%d 个角色就绪（leader + %d 个执行角色）" % (len(roles), len(roles) - 1)
        if not missing_agents else "缺失：%s" % missing_agents)

    skills_dir = os.path.join(core.CODEBUDDY_DIR, "skills")
    expected_skills = [
        "requirement-analysis", "code-explorer", "impact-analysis",
        "task-decomposition", "task-execution", "code-review",
        "test-design", "release-notes", "reflect",
    ]
    missing_skills = [
        name for name in expected_skills
        if not os.path.isfile(os.path.join(skills_dir, name, "SKILL.md"))
    ]
    add("技能配方齐备", not missing_skills,
        "%d 个 skill 就绪" % len(expected_skills)
        if not missing_skills else "缺失：%s" % missing_skills)

    unknown = []
    for stage in core.stage_defs(workflow):
        for spec in stage.get("gates") or []:
            if spec.get("check") not in gates.PRIMITIVES:
                unknown.append("%s/%s" % (stage["id"], spec.get("check")))
    add("门禁原语均已实现", not unknown,
        "全部合法" if not unknown else "未知原语：%s" % unknown)

    for key in (profile.get("doctor_commands") or []):
        command = core.profile_command(profile, key)
        if not command:
            add("命令可用性 %s" % key, False, "profile 未配置 commands.%s" % key)
            continue
        binary = command.strip().split()[0]
        code, out, _ = core.run_cmd("command -v %s" % binary, timeout=15)
        add("命令可用性 %s" % key, code == 0,
            "%s -> %s" % (binary, out if code == 0 else "未找到，需先安装或改 profile"))

    add("经验库可读", True, str(learning.stats_overview()))
    add("仓库地图已生成", os.path.isfile(core.REPO_MAP_FILE),
        core.REPO_MAP_FILE if os.path.isfile(core.REPO_MAP_FILE) else "尚未生成，执行 devflow repo-map")

    failed = [c for c in checks if c["status"] == "FAIL"]
    return core.emit({
        "action": "doctor",
        "overall": "FAIL" if failed else "PASS",
        "checks": checks,
    }, 1 if failed else 0)


# ============================================================
# 参数解析
# ============================================================

def build_parser():
    parser = argparse.ArgumentParser(
        prog="devflow",
        description="devflow — 需求到上线的多 Agent 流水线引擎")
    parser.add_argument("--version", action="version", version="devflow " + VERSION)
    sub = parser.add_subparsers(dest="command")

    def add(name, help_text, func):
        node = sub.add_parser(name, help=help_text)
        node.set_defaults(func=func)
        return node

    p = add("init", "初始化一次需求流水线", cmd_init)
    p.add_argument("--run-id", default="", help="留空自动生成 REQ-YYYYMMDD-NN")
    p.add_argument("--title", required=True)
    p.add_argument("--type", default="feature",
                   choices=["feature", "bugfix", "hotfix", "refactor"])
    p.add_argument("--profile", default="")
    p.add_argument("--requirement", default="", help="需求原文或摘要")
    p.add_argument("--force", action="store_true")

    p = add("next", "取下一阶段的完整 prompt（并标记该阶段开始）", cmd_next)
    p.add_argument("--run-id", default="")
    p.add_argument("--peek", action="store_true", help="只看不改状态、不记注入")

    p = add("gate", "执行阶段门禁（含独立复算与自学习归因）", cmd_gate)
    p.add_argument("--run-id", default="")
    p.add_argument("--stage", required=True)

    p = add("plan-check", "任务拆分结构校验（覆盖率、依赖成环等）", cmd_plan_check)
    p.add_argument("--run-id", default="")
    p.add_argument("--max-files", type=int, default=5)

    p = add("stage-update", "更新阶段状态并推进", cmd_stage_update)
    p.add_argument("--run-id", default="")
    p.add_argument("--stage", required=True)
    p.add_argument("--status", required=True,
                   choices=["running", "passed", "failed", "blocked", "skipped"])
    p.add_argument("--summary", default="")
    p.add_argument("--score", type=int, default=None)
    p.add_argument("--decision", action="append", help="追加一条技术决策到运行上下文")
    p.add_argument("--key-files", action="append", help="追加关键文件到运行上下文")

    p = add("status", "查看当前运行状态", cmd_status)
    p.add_argument("--run-id", default="")

    add("list", "列出所有运行", cmd_list)

    p = add("resume", "中断后恢复（输出恢复上下文 + 下一阶段 prompt）", cmd_resume)
    p.add_argument("--run-id", default="")

    p = add("inspect", "探测阶段产物真实进度（subagent 静默无返回时的兜底）", cmd_inspect)
    p.add_argument("--run-id", default="")
    p.add_argument("--stage", default="")

    p = add("rollback", "回退到指定阶段重做", cmd_rollback)
    p.add_argument("--run-id", default="")
    p.add_argument("--to", required=True)
    p.add_argument("--reason", default="")

    p = add("learn", "手工记录一条学习事件", cmd_learn)
    p.add_argument("--run-id", default="")
    p.add_argument("--stage", required=True)
    p.add_argument("--type", required=True,
                   choices=["gate_fail", "retry", "pitfall", "best_practice",
                            "silent_recovery", "rollback"])
    p.add_argument("--detail", required=True)
    p.add_argument("--fix", default="")
    p.add_argument("--tags", default="")
    p.add_argument("--signature", default="")

    p = add("distill", "蒸馏事件为经验并重算优先级", cmd_distill)
    p.add_argument("--dry-run", action="store_true")

    p = add("learnings", "查询会注入到某阶段的经验", cmd_learnings)
    p.add_argument("--stage", default="*")
    p.add_argument("--tags", default="")
    p.add_argument("--limit", type=int, default=8)

    p = add("reflect", "运行结束复盘：蒸馏 + 淘汰建议", cmd_reflect)
    p.add_argument("--run-id", default="")

    p = add("repo-map", "扫描仓库生成仓库地图", cmd_repo_map)
    p.add_argument("--root", default="")

    add("profiles", "列出可用 profile", cmd_profiles)

    p = add("doctor", "环境自检", cmd_doctor)
    p.add_argument("--profile", default="")

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 2
    try:
        return args.func(args) or 0
    except core.ConfigError as exc:
        return core.emit({"error": str(exc)}, 2)
    except KeyboardInterrupt:
        return core.emit({"error": "已中断"}, 2)


if __name__ == "__main__":
    sys.exit(main())
