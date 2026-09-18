# -*- coding: utf-8 -*-
"""流水线编排。

职责是「给 Leader 算出下一步该干什么」，而不是自己去干。每次 next 调用输出
一个完整可直接投喂给 subagent 的 prompt，里面已经装配好四样东西：

  1. 上游摘要（不是文件路径 —— 下游多数情况不需要重读上游全文）
  2. 本阶段匹配到的历史经验（自学习系统注入）
  3. 仓库地图提示（解决「不知道去哪写代码」）
  4. 本 profile 的真实构建/测试命令（解决「每个部门不一样」）
"""

import os

from . import core, gates, learning, state as state_mod


def _profile_command_block(profile):
    commands = profile.get("commands") or {}
    shown = [(k, v) for k, v in sorted(commands.items()) if v]
    if not shown:
        return "（当前 profile 未配置任何命令，需要执行构建/测试前请先补 profile）"
    return "\n".join("  - %-16s %s" % (k + ":", v) for k, v in shown)


def _repo_map_hint(stage_def):
    if not os.path.isfile(core.REPO_MAP_FILE):
        return "（尚未生成仓库地图，建议先执行 devflow repo-map）"
    sections = stage_def.get("repo_map_sections") or []
    hint = ["仓库地图：%s" % core.REPO_MAP_FILE]
    if sections:
        hint.append("本阶段重点查阅其中的：%s" % "、".join(sections))
    return "\n".join(hint)


def build_variables(state, stage_def, workflow, profile):
    artifacts = stage_def.get("artifacts") or []
    context = state.data.get("context", {})
    return {
        "run_id": state.run_id,
        "title": state.data.get("title", ""),
        "type": state.run_type,
        "stage_id": stage_def["id"],
        "stage_name": stage_def.get("name", ""),
        "role": stage_def.get("role", ""),
        "description": stage_def.get("description", ""),
        "artifacts_dir": state.artifacts_dir,
        "artifacts_list": "\n".join("  - %s" % a for a in artifacts) or "  （本阶段无固定产物）",
        "upstream_summary": context.get("last_summary") or "（首个阶段，无上游）",
        "requirement_summary": context.get("requirement_summary") or "（尚未产出）",
        "blockers": "、".join(context.get("blockers") or []) or "无",
        "profile_name": profile.get("_name", "default"),
        "profile_commands": _profile_command_block(profile),
        "repo_map_hint": _repo_map_hint(stage_def),
        "project_root": core.project_root(),
        "commands": profile.get("commands") or {},
        "profile": profile,
    }


def next_prompt(state, workflow, profile, record=True):
    stage_def = state.next_stage(workflow)
    if stage_def is None:
        return {
            "action": "next",
            "run_id": state.run_id,
            "status": "all_stages_completed",
            "message": "全部阶段已完成，可执行 devflow reflect 做复盘",
        }

    stage_id = stage_def["id"]
    variables = build_variables(state, stage_def, workflow, profile)

    # 自学习注入
    tags = list(stage_def.get("tags") or []) + [stage_id, stage_def.get("role", "")]
    matched = learning.query(stage=stage_id, tags=[t for t in tags if t])
    variables["learnings"] = learning.format_for_prompt(matched) or "（暂无匹配的历史经验）"

    template_name = stage_def.get("prompt") or ("%s.md" % stage_id.lower())
    template_path = os.path.join(core.PROMPTS_DIR, template_name)
    template = core.read_text(template_path)
    if not template:
        template = _fallback_template()

    prompt = core.render(template, variables)

    if record:
        learning.record_injection(state.run_id, stage_id, matched)

    defaults = workflow.get("defaults", {})
    return {
        "action": "next",
        "run_id": state.run_id,
        "stage": stage_id,
        "name": stage_def.get("name", ""),
        "role": stage_def.get("role", ""),
        "subagent": stage_def.get("role", ""),
        "max_turns": stage_def.get("max_turns", defaults.get("max_turns", 25)),
        "artifacts_dir": state.artifacts_dir,
        "expected_artifacts": stage_def.get("artifacts") or [],
        "injected_learnings": [e["id"] for e in matched],
        "attempts_so_far": state.stage(stage_id).get("attempts", 0),
        "prompt": prompt,
    }


def _fallback_template():
    return (
        "你是 devflow 流水线的 {{role}}，当前执行 {{stage_id}} {{stage_name}}。\n\n"
        "需求：{{run_id}} — {{title}}\n"
        "上游摘要：{{upstream_summary}}\n\n"
        "产物目录：{{artifacts_dir}}\n"
        "需要产出：\n{{artifacts_list}}\n\n"
        "{{repo_map_hint}}\n\n"
        "可用命令（来自 profile {{profile_name}}）：\n{{profile_commands}}\n\n"
        "{{learnings}}\n\n"
        "完成后返回 JSON：{\"status\":\"completed|failed|blocked\","
        "\"summary\":\"200字内\",\"artifacts\":[],\"issues\":[]}\n"
    )


# ============================================================
# 门禁 + 推进
# ============================================================

def run_stage_gate(state, stage_id, workflow, profile):
    """执行门禁，自动落学习事件，自动做经验有效性归因。"""
    stage_def = core.find_stage(stage_id, workflow)
    if stage_def is None:
        raise core.ConfigError("未知阶段 '%s'" % stage_id)

    variables = build_variables(state, stage_def, workflow, profile)
    results = gates.run_gates(stage_def, state.artifacts_dir, profile, variables)

    if stage_def.get("plan_check"):
        plan_cfg = stage_def["plan_check"] if isinstance(stage_def["plan_check"], dict) else {}
        plan = gates.run_plan_check(
            state.artifacts_dir,
            tasks_file=plan_cfg.get("tasks_file", "tasks.yaml"),
            depmap_file=plan_cfg.get("dependency_map_file", "dependency-map.yaml"),
            max_files_per_task=plan_cfg.get("max_files_per_task", 5),
        )
        for item in plan["checks"]:
            results.append(gates.GateResult(
                item["check"], item["status"], item["evidence"],
                item["message"], item["severity"]))

    summary = gates.summarize(results, stage_id)

    # 采集：门禁失败自动转学习事件，不依赖 Agent 自觉
    events = learning.record_gate_failures(state.run_id, stage_id, summary)
    # 回收：本阶段注入过的经验，这次是否真的起作用了
    recycle = learning.feedback(state.run_id, stage_id, summary)

    summary["learning"] = {
        "events_recorded": len(events),
        "recycled": recycle,
    }
    core.write_json(os.path.join(core.run_dir(state.run_id), "gates", "%s.json" % stage_id), summary)
    return summary


def advance(state, stage_id, status, workflow, summary_text="", score=None, artifacts=None):
    stage_def = core.find_stage(stage_id, workflow)
    artifacts = artifacts or []
    if not artifacts and stage_def:
        for name in stage_def.get("artifacts") or []:
            path = os.path.join(state.artifacts_dir, name)
            if os.path.isfile(path):
                artifacts.append(name)

    state.finish_stage(stage_id, status, summary_text, score, artifacts)

    if status == "passed":
        nxt = state.next_stage(workflow)
        if nxt:
            state.transition(stage_id, nxt["id"], "门禁通过")
        else:
            state.data["current_stage"] = None
            state.save()
    return state.summary(workflow)


# ============================================================
# 中断恢复
# ============================================================

def inspect_stage_artifacts(state, stage_id, workflow):
    """探测阶段产物的真实情况。

    这是「subagent 静默死亡」的兜底依据：当 task() 返回空或被截断时，
    不要原地等用户说「继续」，而是回来看产物到底写出来没有。
    """
    stage_def = core.find_stage(stage_id, workflow) or {}
    expected = stage_def.get("artifacts") or []
    present, missing = [], []
    for name in expected:
        path = os.path.join(state.artifacts_dir, name)
        if os.path.isfile(path) and len(core.read_text(path).strip()) > 50:
            present.append(name)
        else:
            missing.append(name)
    if not expected:
        verdict = "no_artifacts_expected"
    elif not missing:
        verdict = "complete"
    elif present:
        verdict = "partial"
    else:
        verdict = "empty"
    return {
        "stage": stage_id,
        "verdict": verdict,
        "present": present,
        "missing": missing,
        "recommendation": {
            "complete": "产物齐备 —— 直接跑门禁并推进，不要重跑 subagent",
            "partial": "产物不全 —— 带着已完成部分重新调用 subagent 补齐缺失项",
            "empty": "产物缺失 —— 提高 max_turns 后重新调用 subagent",
            "no_artifacts_expected": "本阶段无固定产物 —— 依据返回结果判断",
        }[verdict],
    }


def resume(state, workflow, profile):
    completed = []
    for stage_def in core.stage_defs(workflow):
        info = state.stage(stage_def["id"])
        if info.get("status") == "passed":
            completed.append({
                "stage": stage_def["id"],
                "name": stage_def.get("name", ""),
                "summary": info.get("summary", ""),
                "score": info.get("gate_score"),
            })

    payload = next_prompt(state, workflow, profile)
    payload["action"] = "resume"
    payload["completed_stages"] = completed
    payload["context"] = state.data.get("context", {})
    if payload.get("stage"):
        payload["artifact_inspection"] = inspect_stage_artifacts(state, payload["stage"], workflow)
        payload["message"] = "从 %s 恢复（已完成 %d 个阶段，无需重做）" % (
            payload["stage"], len(completed))
    return payload
