# -*- coding: utf-8 -*-
"""自学习闭环。

早期原型已经有「事件日志 -> 经验库 -> 分级注入」，但缺了两环，导致它更像
一个只进不出的经验垃圾堆：

  - 蒸馏靠人手动跑，高频问题不会自己浮上来；
  - 经验注入之后没人回收效果，一条没用的经验会永远占着 P0 消耗 token。

这里补上的是后半个闭环：

  采集(自动) -> 蒸馏(按签名聚类) -> 升级(跨 run 频次) -> 注入(分级)
       ^                                                      |
       |                                                      v
  淘汰/改写 <---------------- 有效性回收(注入后该门禁是否仍失败) <--+

关键概念是 **signature**：形如 `gate_fail:S2:CHECK-1`，把「事件」和「经验」
和「后续门禁结果」三者精确对齐，使得效果归因不靠模糊的文本匹配。
"""

import hashlib
import json
import os
import re

from . import core

PRIORITIES = ["P0", "P1", "P2"]
STATUS_ACTIVE = "active"
STATUS_NEEDS_REWRITE = "needs_rewrite"
STATUS_RETIRED = "retired"

DEFAULT_RULES = {
    "promote_to_p1_runs": 2,      # 在 N 个不同 run 中复现 -> 升 P1
    "promote_to_p0_runs": 3,      # 在 N 个不同 run 中复现 -> 考虑升 P0
    "promote_to_p0_stages": 2,    # 且跨 N 个不同阶段 -> 升 P0（全局铁律）
    "distill_min_hits": 2,        # 至少命中 N 次才生成候选经验
    "demote_after_failures": 2,   # 注入后仍失败 N 次 -> 降级并标记待改写
    "retire_after_failures": 4,   # 注入后仍失败 N 次 -> 淘汰
    "proven_streak": 3,           # 连续 N 次注入后门禁通过 -> 固化
    "max_inject": 8,              # 单次注入条数上限（控制 prompt 体积）
}


def rules():
    try:
        configured = core.load_workflow().get("learning", {}) or {}
    except core.ConfigError:
        configured = {}
    merged = dict(DEFAULT_RULES)
    merged.update(dict((k, v) for k, v in configured.items() if k in DEFAULT_RULES))
    return merged


# ============================================================
# 事件采集
# ============================================================

def _auto_signature(event_type, stage, detail):
    """没有显式签名时，用 detail 的指纹兜底。

    只按 (类型, 阶段) 生成签名会把同一阶段的不同问题错误地聚成一簇，
    蒸馏出来的经验就会变成几个问题的糊状混合体。加上 detail 指纹后，
    措辞相同的问题仍能聚合，不同的问题则各自成簇。
    """
    digest = hashlib.md5((detail or "").encode("utf-8")).hexdigest()[:8]
    return "%s:%s:%s" % (event_type, stage, digest)


def record_event(run_id, stage, event_type, detail, fix="", tags=None, signature=""):
    """追加一条学习事件。由门禁/编排器自动调用，不依赖 Agent 自觉上报。"""
    core.ensure_dir(core.EVENTS_DIR)
    event = {
        "at": core.now_iso(),
        "run_id": run_id,
        "stage": stage,
        "event_type": event_type,
        "signature": signature or _auto_signature(event_type, stage, detail),
        "detail": (detail or "")[:500],
        "fix": (fix or "")[:500],
        "tags": tags or [],
    }
    path = os.path.join(core.EVENTS_DIR, core.today() + ".jsonl")
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")
    return event


def record_gate_failures(run_id, stage, gate_summary):
    """把门禁失败项自动转成学习事件 —— 采集环节的自动化入口。"""
    events = []
    for item in gate_summary.get("blocking", []):
        events.append(record_event(
            run_id=run_id,
            stage=stage,
            event_type="gate_fail",
            detail="%s：%s" % (item["check"], item.get("evidence", "")),
            fix=item.get("message", ""),
            tags=["gate", stage],
            signature="gate_fail:%s:%s" % (stage, item["check"]),
        ))
    return events


def load_events():
    events = []
    if not os.path.isdir(core.EVENTS_DIR):
        return events
    for name in sorted(os.listdir(core.EVENTS_DIR)):
        if not name.endswith(".jsonl"):
            continue
        for line in core.read_text(os.path.join(core.EVENTS_DIR, name)).splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except ValueError:
                continue
    return events


# ============================================================
# 经验库读写
# ============================================================

def load_entries():
    data = core.read_yaml(core.ACTIVE_LEARNINGS_FILE, default={}) or {}
    entries = data.get("entries") or []
    for entry in entries:
        entry.setdefault("status", STATUS_ACTIVE)
        entry.setdefault("stats", {})
        for key in ("hits", "runs", "injected", "fail_after_inject", "effective_streak"):
            entry["stats"].setdefault(key, 0)
        entry["stats"].setdefault("proven", False)
        entry["stats"].setdefault("run_ids", [])
        entry["stats"].setdefault("stages", [])
    return entries


def save_entries(entries):
    """手写 YAML 发射器：保留头部说明、控制字段顺序，让经验库始终是人可读可改的。"""
    lines = [
        "# devflow 活跃经验库（自学习闭环的持久化层）",
        "#",
        "# priority: P0=每次必读 / P1=匹配阶段时读 / P2=匹配标签时读",
        "# status:   active=生效 / needs_rewrite=注入后仍失败待改写 / retired=已淘汰",
        "# signature: 把事件、经验、后续门禁结果三者对齐的归因键",
        "#",
        "# 本文件由 `devflow distill` 自动维护，也可手工编辑；",
        "# 手工新增条目请把 origin 设为 manual，蒸馏时不会覆盖。",
        "",
        "version: 1",
        "updated: \"%s\"" % core.today(),
        "",
        "entries:",
    ]
    if not entries:
        lines.append("  []")
    for entry in entries:
        stats = entry.get("stats", {})
        lines.append("")
        lines.append("  - id: %s" % entry["id"])
        lines.append("    priority: %s" % entry.get("priority", "P2"))
        lines.append("    status: %s" % entry.get("status", STATUS_ACTIVE))
        lines.append("    stage: %s" % _yaml_scalar(entry.get("stage", "*")))
        lines.append("    signature: %s" % _yaml_scalar(entry.get("signature", "")))
        lines.append("    pattern: %s" % _yaml_scalar(entry.get("pattern", "")))
        lines.append("    fix: %s" % _yaml_scalar(entry.get("fix", "")))
        lines.append("    tags: [%s]" % ", ".join(_yaml_scalar(t) for t in entry.get("tags", [])))
        lines.append("    origin: %s" % entry.get("origin", "auto"))
        lines.append("    created: \"%s\"" % entry.get("created", core.today()))
        lines.append("    updated: \"%s\"" % entry.get("updated", core.today()))
        lines.append("    stats:")
        lines.append("      hits: %d" % stats.get("hits", 0))
        lines.append("      runs: %d" % stats.get("runs", 0))
        lines.append("      injected: %d" % stats.get("injected", 0))
        lines.append("      fail_after_inject: %d" % stats.get("fail_after_inject", 0))
        lines.append("      effective_streak: %d" % stats.get("effective_streak", 0))
        lines.append("      proven: %s" % ("true" if stats.get("proven") else "false"))
        lines.append("      run_ids: [%s]" % ", ".join(_yaml_scalar(r) for r in stats.get("run_ids", [])[-10:]))
        lines.append("      stages: [%s]" % ", ".join(_yaml_scalar(s) for s in stats.get("stages", [])))
    core.write_text(core.ACTIVE_LEARNINGS_FILE, "\n".join(lines) + "\n")


# 只有以字母数字或下划线开头、且全程不含 YAML 特殊字符的串才敢裸写。
# 尤其注意 "*" 和 "&"：裸写会被解析成别名/锚点，而 stage 字段恰好常取 "*"。
_SAFE_SCALAR = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_\-./]*$")


def _yaml_scalar(value):
    text = "" if value is None else str(value)
    if text and _SAFE_SCALAR.match(text):
        return text
    return '"%s"' % text.replace("\\", "\\\\").replace('"', '\\"')


def _next_id(entries):
    used = set()
    for entry in entries:
        match = re.match(r"^L-(\d+)$", str(entry.get("id", "")))
        if match:
            used.add(int(match.group(1)))
    index = 1
    while index in used:
        index += 1
    return "L-%03d" % index


# ============================================================
# 蒸馏 + 分级
# ============================================================

def distill(apply_changes=True):
    """按 signature 聚类事件，生成/更新经验条目并重算优先级。"""
    cfg = rules()
    events = load_events()
    entries = load_entries()
    by_signature = dict((e.get("signature"), e) for e in entries if e.get("signature"))

    clusters = {}
    for event in events:
        if event.get("event_type") not in ("gate_fail", "retry", "pitfall",
                                           "silent_recovery", "rollback"):
            continue
        sig = event.get("signature")
        if not sig:
            continue
        bucket = clusters.setdefault(sig, {
            "signature": sig,
            "hits": 0,
            "run_ids": [],
            "stages": [],
            "details": [],
            "fixes": [],
            "tags": [],
        })
        bucket["hits"] += 1
        for key, value in (("run_ids", event.get("run_id")), ("stages", event.get("stage"))):
            if value and value not in bucket[key]:
                bucket[key].append(value)
        if event.get("detail"):
            bucket["details"].append(event["detail"])
        if event.get("fix") and event["fix"] not in bucket["fixes"]:
            bucket["fixes"].append(event["fix"])
        for tag in event.get("tags") or []:
            if tag not in bucket["tags"]:
                bucket["tags"].append(tag)

    created, updated, promoted = [], [], []

    for sig, bucket in sorted(clusters.items()):
        if bucket["hits"] < cfg["distill_min_hits"]:
            continue
        entry = by_signature.get(sig)
        if entry is None:
            entry = {
                "id": _next_id(entries),
                "priority": "P2",
                "status": STATUS_ACTIVE,
                "stage": bucket["stages"][0] if len(bucket["stages"]) == 1 else "*",
                "signature": sig,
                "pattern": _summarize(bucket["details"]),
                "fix": bucket["fixes"][0] if bucket["fixes"] else "（待补充修复建议）",
                "tags": bucket["tags"][:6],
                "origin": "auto",
                "created": core.today(),
                "updated": core.today(),
                "stats": {"hits": 0, "runs": 0, "injected": 0, "fail_after_inject": 0,
                          "effective_streak": 0, "proven": False, "run_ids": [], "stages": []},
            }
            entries.append(entry)
            by_signature[sig] = entry
            created.append(entry["id"])
        elif entry.get("status") == STATUS_RETIRED:
            continue
        else:
            updated.append(entry["id"])
            if entry.get("origin") != "manual" and bucket["fixes"]:
                entry["fix"] = entry.get("fix") or bucket["fixes"][0]

        stats = entry["stats"]
        stats["hits"] = bucket["hits"]
        stats["run_ids"] = bucket["run_ids"]
        stats["stages"] = bucket["stages"]
        stats["runs"] = len(bucket["run_ids"])
        entry["updated"] = core.today()

        before = entry["priority"]
        entry["priority"] = _grade(entry, cfg)
        if entry["priority"] == "P0":
            entry["stage"] = "*"
        if entry["priority"] != before:
            promoted.append({"id": entry["id"], "from": before, "to": entry["priority"],
                             "reason": "在 %d 个 run / %d 个阶段复现" % (stats["runs"], len(stats["stages"]))})

    if apply_changes:
        save_entries(entries)

    return {
        "events_scanned": len(events),
        "clusters": len(clusters),
        "created": created,
        "updated": updated,
        "promoted": promoted,
        "total_entries": len(entries),
        "applied": apply_changes,
    }


def _grade(entry, cfg):
    """根据跨 run / 跨阶段复现程度定级。已固化(proven)的条目不再降级。"""
    stats = entry["stats"]
    if entry.get("origin") == "manual" and entry.get("priority") == "P0":
        return "P0"
    if stats["runs"] >= cfg["promote_to_p0_runs"] and \
            len(stats.get("stages", [])) >= cfg["promote_to_p0_stages"]:
        return "P0"
    if stats["runs"] >= cfg["promote_to_p1_runs"]:
        return "P1"
    return "P2"


def _summarize(details):
    if not details:
        return "（无描述）"
    # 取最短的一条作为模式描述：通常最短的那条最接近问题本质，噪音最少
    return sorted(details, key=len)[0][:160]


# ============================================================
# 注入
# ============================================================

def query(stage="*", tags=None, limit=None):
    """按阶段/标签挑选要注入的经验。P0 全局命中，P1 匹配阶段，P2 匹配标签。"""
    cfg = rules()
    limit = limit or cfg["max_inject"]
    tags = set(tags or [])
    ranked = []
    for entry in load_entries():
        if entry.get("status") == STATUS_RETIRED:
            continue
        priority = entry.get("priority", "P2")
        entry_stage = entry.get("stage", "*")
        entry_tags = set(entry.get("tags") or [])
        if priority == "P0":
            rank = 0
        elif priority == "P1" and entry_stage in ("*", stage):
            rank = 1
        elif priority == "P2" and (entry_tags & tags or entry_stage == stage):
            rank = 2
        else:
            continue
        # 同级内，命中次数多的排前面；待改写的排后面
        penalty = 1 if entry.get("status") == STATUS_NEEDS_REWRITE else 0
        ranked.append((rank, penalty, -entry["stats"].get("hits", 0), entry))
    ranked.sort(key=lambda item: item[:3])
    return [item[3] for item in ranked[:limit]]


def format_for_prompt(entries):
    """渲染成注入 prompt 的文本块。"""
    if not entries:
        return ""
    lines = ["📚 历史经验（自学习系统按本阶段自动匹配，请对照避坑）："]
    for entry in entries:
        flag = " [待改写]" if entry.get("status") == STATUS_NEEDS_REWRITE else ""
        proven = " [已验证]" if entry.get("stats", {}).get("proven") else ""
        lines.append("  - [%s|%s]%s%s %s" % (
            entry["id"], entry.get("priority", "P2"), proven, flag, entry.get("pattern", "")))
        if entry.get("fix"):
            lines.append("      对策：%s" % entry["fix"])
    return "\n".join(lines)


def record_injection(run_id, stage, entries):
    """登记本次注入了哪些经验，作为后续效果归因的依据。"""
    if not entries:
        return []
    ids = [e["id"] for e in entries]
    all_entries = load_entries()
    index = dict((e["id"], e) for e in all_entries)
    for eid in ids:
        entry = index.get(eid)
        if entry:
            entry["stats"]["injected"] = entry["stats"].get("injected", 0) + 1
    save_entries(all_entries)
    core.write_json(_injection_path(run_id, stage), {
        "run_id": run_id, "stage": stage, "at": core.now_iso(), "ids": ids,
        "signatures": [index[i].get("signature") for i in ids if i in index],
    })
    return ids


def _injection_path(run_id, stage):
    return os.path.join(core.run_dir(run_id), "injections", "%s.json" % stage)


# ============================================================
# 有效性回收
# ============================================================

def feedback(run_id, stage, gate_summary):
    """门禁跑完后归因：本阶段注入过的经验，对应问题是否真的被避免了。

    命中即失败 -> 这条经验没起作用，降级并标记待改写；
    连续有效   -> 固化为 proven，不再参与降级。
    """
    injection = core.read_json(_injection_path(run_id, stage))
    if not injection or not injection.get("ids"):
        return {"stage": stage, "evaluated": 0, "demoted": [], "proven": [], "retired": []}

    cfg = rules()
    failed_signatures = set(
        "gate_fail:%s:%s" % (stage, item["check"])
        for item in gate_summary.get("blocking", [])
    )

    entries = load_entries()
    index = dict((e["id"], e) for e in entries)
    demoted, proven, retired = [], [], []

    for eid in injection["ids"]:
        entry = index.get(eid)
        if not entry:
            continue
        stats = entry["stats"]
        signature = entry.get("signature", "")
        # 只对「本阶段相关」的经验做归因，避免误伤跨阶段的 P0 铁律
        relevant = signature in failed_signatures or signature.startswith("gate_fail:%s:" % stage)
        if not relevant:
            continue

        if signature in failed_signatures:
            stats["fail_after_inject"] = stats.get("fail_after_inject", 0) + 1
            stats["effective_streak"] = 0
            if stats["fail_after_inject"] >= cfg["retire_after_failures"]:
                entry["status"] = STATUS_RETIRED
                retired.append({"id": eid, "reason": "注入 %d 次仍未避免同一问题" % stats["fail_after_inject"]})
            elif stats["fail_after_inject"] >= cfg["demote_after_failures"] and not stats.get("proven"):
                entry["status"] = STATUS_NEEDS_REWRITE
                entry["priority"] = _demote(entry.get("priority", "P2"))
                demoted.append({"id": eid, "to": entry["priority"],
                                "reason": "注入后同一门禁仍失败 %d 次，经验描述可能没抓住本质"
                                          % stats["fail_after_inject"]})
        else:
            stats["effective_streak"] = stats.get("effective_streak", 0) + 1
            if stats["effective_streak"] >= cfg["proven_streak"] and not stats.get("proven"):
                stats["proven"] = True
                entry["status"] = STATUS_ACTIVE
                proven.append({"id": eid, "reason": "连续 %d 次注入后该门禁未再失败"
                                                    % stats["effective_streak"]})
        entry["updated"] = core.today()

    save_entries(entries)
    return {
        "stage": stage,
        "evaluated": len(injection["ids"]),
        "demoted": demoted,
        "proven": proven,
        "retired": retired,
    }


def _demote(priority):
    if priority not in PRIORITIES:
        return "P2"
    return PRIORITIES[min(PRIORITIES.index(priority) + 1, len(PRIORITIES) - 1)]


# ============================================================
# 复盘
# ============================================================

def reflect(run_id):
    """一次运行结束后的复盘：本轮产生了什么新经验、哪些经验该淘汰。"""
    events = [e for e in load_events() if e.get("run_id") == run_id]
    entries = load_entries()
    by_stage = {}
    for event in events:
        by_stage.setdefault(event.get("stage", "?"), []).append(event)

    return {
        "run_id": run_id,
        "events_this_run": len(events),
        "events_by_stage": dict((k, len(v)) for k, v in sorted(by_stage.items())),
        "signatures_this_run": sorted(set(e.get("signature") for e in events if e.get("signature"))),
        "library": stats_overview(entries),
        "needs_rewrite": [
            {"id": e["id"], "pattern": e.get("pattern"), "fail_after_inject": e["stats"].get("fail_after_inject")}
            for e in entries if e.get("status") == STATUS_NEEDS_REWRITE
        ],
        "retired": [{"id": e["id"], "pattern": e.get("pattern")}
                    for e in entries if e.get("status") == STATUS_RETIRED],
    }


def stats_overview(entries=None):
    entries = entries if entries is not None else load_entries()
    counts = {"P0": 0, "P1": 0, "P2": 0}
    for entry in entries:
        if entry.get("status") == STATUS_RETIRED:
            continue
        counts[entry.get("priority", "P2")] = counts.get(entry.get("priority", "P2"), 0) + 1
    return {
        "total": len(entries),
        "active": len([e for e in entries if e.get("status") == STATUS_ACTIVE]),
        "needs_rewrite": len([e for e in entries if e.get("status") == STATUS_NEEDS_REWRITE]),
        "retired": len([e for e in entries if e.get("status") == STATUS_RETIRED]),
        "proven": len([e for e in entries if e.get("stats", {}).get("proven")]),
        "by_priority": counts,
    }
