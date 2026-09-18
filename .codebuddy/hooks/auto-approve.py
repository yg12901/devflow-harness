#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CodeBuddy PreToolUse hook —— 命令自动放行（黑名单模式）。

解决的问题：流水线跑到一半弹出「是否允许执行 xxx？」然后停在那儿等人点确认，
是端到端自动化最常见的中断点之一。

策略是黑名单而非白名单：默认放行，只拦截真正危险的命令。白名单模式在实践中
会不断因为漏配某条命令而卡住流程，维护成本高且收益低。

危险命令来源有两部分：
  1. 本文件里的 BASE_DENY —— 与技术栈无关的通用红线
  2. 当前 profile 的 vcs.deny —— 各部门自定义的红线

输入（stdin，JSON）：{"tool_name": "Bash", "tool_input": {"command": "git status"}}
输出（stdout，JSON）：{"continue": true, "permissionDecision": "allow"|"deny"}
"""

import json
import os
import re
import sys

# 与技术栈无关的通用红线。正则匹配，大小写不敏感。
BASE_DENY = [
    # 毁灭性删除
    (r"\brm\s+(-[a-zA-Z]*\s+)*-[a-zA-Z]*[rf][a-zA-Z]*\s+/(\s|$)", "删除根目录"),
    (r"\brm\s+-[a-zA-Z]*[rf][a-zA-Z]*\s+(~|\$HOME)(/\s*)?(\s|$)", "删除用户主目录"),
    (r":\(\)\s*\{.*\};\s*:", "fork 炸弹"),
    (r"\bmkfs(\.\w+)?\b", "格式化文件系统"),
    (r"\bdd\b.*\bof=/dev/(sd|nvme|hd)", "直写块设备"),
    (r">\s*/dev/(sd|nvme|hd)\w*", "直写块设备"),

    # 不可逆的历史与远端操作
    (r"\bgit\s+push\s+.*(--force\b|-f\b)", "强推会覆盖远端历史"),
    (r"\bgit\s+reset\s+--hard\b", "硬重置会丢弃未提交改动"),
    (r"\bgit\s+clean\s+-[a-zA-Z]*f", "清理未跟踪文件不可恢复"),
    (r"\bgit\s+filter-branch\b", "重写历史"),

    # 权限与凭据
    (r"\bchmod\s+-R\s+777\s+/(\s|$)", "放开根目录权限"),
    (r"\bcurl\b[^|]*\|\s*(sudo\s+)?(ba)?sh", "管道执行远端脚本"),
    (r"\bwget\b[^|]*\|\s*(sudo\s+)?(ba)?sh", "管道执行远端脚本"),

    # 关机重启
    (r"\b(shutdown|reboot|halt|poweroff)\b", "关机/重启"),
]


def load_profile_deny():
    """从当前默认 profile 读取 vcs.deny，让各部门能自定义红线。"""
    here = os.path.dirname(os.path.abspath(__file__))
    devflow_dir = os.path.join(os.path.dirname(here), "devflow")
    try:
        sys.path.insert(0, devflow_dir)
        from engine import core
        profile = core.load_profile()
        return [(re.escape(item.strip()), "profile 红线：%s" % item.strip())
                for item in (profile.get("vcs") or {}).get("deny", []) if item]
    except Exception:
        # profile 读不到不应该阻塞命令执行，静默降级到只用基础黑名单
        return []


def decide(command):
    if not command or not command.strip():
        return "allow", ""
    text = command.strip()
    for pattern, reason in BASE_DENY + load_profile_deny():
        if re.search(pattern, text, re.IGNORECASE):
            return "deny", reason
    return "allow", ""


def main():
    try:
        payload = json.load(sys.stdin)
    except (ValueError, IOError):
        # 读不懂输入就放行，hook 不应该成为新的卡点
        print(json.dumps({"continue": True, "permissionDecision": "allow"}))
        return 0

    tool_input = payload.get("tool_input") or {}
    command = tool_input.get("command") or tool_input.get("cmd") or ""
    decision, reason = decide(command)

    result = {"continue": True, "permissionDecision": decision}
    if decision == "deny":
        result["permissionDecisionReason"] = (
            "devflow 安全钩子拦截：%s。如确实需要，请手工执行。" % reason)
        sys.stderr.write("[devflow] 已拦截危险命令（%s）：%s\n" % (reason, command))

    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
