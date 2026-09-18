# -*- coding: utf-8 -*-
"""运行态单一真相源。

一次运行只写一份 run-state.json。所有读写都走 RunState，其余模块不得直接改文件。
会话中断后只认这一处，避免进度记录和产物目录互相矛盾。

文件锁用 fcntl（POSIX）；Windows 下降级为无锁写入，不阻塞主流程。
"""

import json
import os

from . import core

DONE = ("passed", "skipped")
TERMINAL = ("passed", "skipped", "failed", "blocked")
STATE_FILENAME = "run-state.json"


def state_path(run_id):
    return os.path.join(core.run_dir(run_id), STATE_FILENAME)


def artifacts_dir(run_id):
    return os.path.join(core.run_dir(run_id), "artifacts")


class RunState(object):
    """一次需求流水线运行的完整状态。"""

    def __init__(self, data, path):
        self.data = data
        self.path = path

    # ---------- 生命周期 ----------

    @classmethod
    def create(cls, run_id, title, run_type, profile_name, workflow):
        stages = {}
        skip = core.skipped_stages(run_type, workflow)
        for stage in core.stage_defs(workflow):
            sid = stage["id"]
            stages[sid] = {
                "status": "skipped" if sid in skip else "pending",
                "role": stage.get("role", ""),
                "name": stage.get("name", ""),
                "started_at": None,
                "completed_at": None,
                "duration_ms": None,
                "attempts": 0,
                "gate_score": None,
                "gate_passed": None,
                "summary": "",
                "artifacts": [],
            }
        active = core.active_stages(run_type, workflow)
        data = {
            "schema": 1,
            "run_id": run_id,
            "title": title,
            "type": run_type,
            "profile": profile_name,
            "status": "running",
            "created_at": core.now_iso(),
            "updated_at": core.now_iso(),
            "current_stage": active[0]["id"] if active else None,
            # context 是跨会话恢复的核心：断点续跑时无需重读全部产物
            "context": {
                "requirement_summary": "",
                "technical_decisions": [],
                "key_files": [],
                "acceptance_criteria": [],
                "blockers": [],
                "last_summary": "",
            },
            "stages": stages,
            "transitions": [],
            "events": [],
        }
        state = cls(data, state_path(run_id))
        core.ensure_dir(artifacts_dir(run_id))
        state.save()
        return state

    @classmethod
    def load(cls, run_id):
        path = state_path(run_id)
        data = core.read_json(path)
        if data is None:
            raise core.ConfigError(
                "找不到运行 '%s'，先执行 devflow init 初始化" % run_id)
        return cls(data, path)

    @classmethod
    def load_or_latest(cls, run_id=None):
        """run_id 为空时自动选择最近一个 running 的运行，支持 `devflow resume`。"""
        if run_id:
            return cls.load(run_id)
        candidates = []
        if os.path.isdir(core.RUNS_DIR):
            for name in os.listdir(core.RUNS_DIR):
                data = core.read_json(state_path(name))
                if data:
                    candidates.append(data)
        if not candidates:
            raise core.ConfigError("没有任何运行记录")
        running = [c for c in candidates if c.get("status") == "running"]
        pool = running or candidates
        pool.sort(key=lambda c: c.get("updated_at") or "", reverse=True)
        return cls.load(pool[0]["run_id"])

    @staticmethod
    def list_runs():
        runs = []
        if not os.path.isdir(core.RUNS_DIR):
            return runs
        for name in sorted(os.listdir(core.RUNS_DIR)):
            data = core.read_json(state_path(name))
            if data:
                runs.append({
                    "run_id": data.get("run_id"),
                    "title": data.get("title"),
                    "status": data.get("status"),
                    "current_stage": data.get("current_stage"),
                    "progress": RunState(data, "").progress(),
                    "updated_at": data.get("updated_at"),
                })
        return runs

    def save(self):
        self.data["updated_at"] = core.now_iso()
        core.ensure_dir(os.path.dirname(self.path))
        payload = json.dumps(self.data, ensure_ascii=False, indent=2) + "\n"
        try:
            import fcntl
            with open(self.path, "w", encoding="utf-8") as handle:
                fcntl.flock(handle, fcntl.LOCK_EX)
                try:
                    handle.write(payload)
                finally:
                    fcntl.flock(handle, fcntl.LOCK_UN)
        except ImportError:
            core.write_text(self.path, payload)

    # ---------- 便捷访问 ----------

    @property
    def run_id(self):
        return self.data["run_id"]

    @property
    def run_type(self):
        return self.data.get("type", "feature")

    @property
    def profile_name(self):
        return self.data.get("profile", "default")

    @property
    def artifacts_dir(self):
        return artifacts_dir(self.run_id)

    def stage(self, stage_id):
        return self.data["stages"].setdefault(stage_id, {"status": "pending"})

    def progress(self):
        stages = self.data.get("stages", {})
        if not stages:
            return 0
        done = sum(1 for s in stages.values() if s.get("status") in DONE)
        return int(round(done * 100.0 / len(stages)))

    # ---------- 状态流转 ----------

    def next_stage(self, workflow):
        """返回下一个待执行阶段定义；全部完成返回 None。"""
        for stage in core.active_stages(self.run_type, workflow):
            if self.stage(stage["id"]).get("status") not in DONE:
                return stage
        return None

    def start_stage(self, stage_id):
        stage = self.stage(stage_id)
        stage["status"] = "running"
        stage["started_at"] = core.now_iso()
        stage["attempts"] = int(stage.get("attempts") or 0) + 1
        self.data["current_stage"] = stage_id
        self.add_event("stage_start", stage_id, "阶段启动（第 %d 次尝试）" % stage["attempts"])
        self.save()
        return stage

    def finish_stage(self, stage_id, status, summary="", score=None, artifacts=None):
        stage = self.stage(stage_id)
        previous = stage.get("status")
        stage["status"] = status
        stage["completed_at"] = core.now_iso()
        stage["duration_ms"] = core.elapsed_ms(stage.get("started_at"))
        if summary:
            stage["summary"] = summary
        if score is not None:
            stage["gate_score"] = score
            stage["gate_passed"] = status == "passed"
        if artifacts:
            stage["artifacts"] = artifacts

        if status == "passed" and summary:
            # 下游阶段靠这条摘要拿上游结论，避免全量重读产物
            self.data["context"]["last_summary"] = summary

        if status in ("failed", "blocked"):
            blockers = self.data["context"].setdefault("blockers", [])
            entry = "%s: %s" % (stage_id, summary or status)
            if entry not in blockers:
                blockers.append(entry)
        else:
            self.data["context"]["blockers"] = [
                b for b in self.data["context"].get("blockers", [])
                if not b.startswith(stage_id + ":")
            ]

        self.add_event(
            "stage_%s" % status, stage_id,
            summary or ("%s -> %s" % (previous, status)))
        self.refresh_status()
        self.save()
        return stage

    def refresh_status(self):
        stages = self.data.get("stages", {})
        statuses = [s.get("status") for s in stages.values()]
        if any(s == "blocked" for s in statuses):
            self.data["status"] = "blocked"
        elif any(s == "failed" for s in statuses):
            self.data["status"] = "failed"
        elif all(s in DONE for s in statuses):
            self.data["status"] = "completed"
            self.data["current_stage"] = None
        else:
            self.data["status"] = "running"

    def transition(self, from_stage, to_stage, reason=""):
        self.data["transitions"].append({
            "from": from_stage,
            "to": to_stage,
            "at": core.now_iso(),
            "reason": reason,
        })
        self.data["current_stage"] = to_stage
        self.save()

    def rollback(self, target_stage, workflow, reason=""):
        """回退到目标阶段：清空它及其下游的状态，产物保留并标记 superseded。"""
        order = [s["id"] for s in core.stage_defs(workflow)]
        if target_stage not in order:
            raise core.ConfigError("未知阶段 '%s'，可选：%s" % (target_stage, ", ".join(order)))
        index = order.index(target_stage)
        cleared = []
        for sid in order[index:]:
            stage = self.data["stages"].get(sid)
            if not stage or stage.get("status") in ("pending", "skipped"):
                continue
            cleared.append(sid)
            if stage.get("artifacts"):
                stage["artifacts_note"] = "superseded_by_rollback"
            stage.update({
                "status": "pending",
                "started_at": None,
                "completed_at": None,
                "duration_ms": None,
                "gate_score": None,
                "gate_passed": None,
                "summary": "",
            })
        self.data["context"]["blockers"] = []
        self.transition(self.data.get("current_stage"), target_stage,
                        reason or "手动回退")
        self.add_event("rollback", target_stage,
                       "回退到 %s，清空 %d 个阶段：%s" % (target_stage, len(cleared), ", ".join(cleared)))
        self.refresh_status()
        self.save()
        return cleared

    def update_context(self, **kwargs):
        for key, value in kwargs.items():
            if value:
                self.data["context"][key] = value
        self.save()

    def add_event(self, event_type, stage_id, detail):
        self.data.setdefault("events", []).append({
            "at": core.now_iso(),
            "type": event_type,
            "stage": stage_id,
            "detail": detail,
        })
        # 事件日志只保留最近 200 条，避免状态文件无限膨胀
        if len(self.data["events"]) > 200:
            self.data["events"] = self.data["events"][-200:]

    # ---------- 摘要 ----------

    def summary(self, workflow):
        stages = []
        for stage in core.stage_defs(workflow):
            sid = stage["id"]
            info = self.stage(sid)
            stages.append({
                "id": sid,
                "name": stage.get("name", ""),
                "role": stage.get("role", ""),
                "status": info.get("status", "pending"),
                "attempts": info.get("attempts", 0),
                "gate_score": info.get("gate_score"),
                "duration_ms": info.get("duration_ms"),
                "summary": info.get("summary", ""),
            })
        return {
            "run_id": self.run_id,
            "title": self.data.get("title"),
            "type": self.run_type,
            "profile": self.profile_name,
            "status": self.data.get("status"),
            "current_stage": self.data.get("current_stage"),
            "progress": self.progress(),
            "blockers": self.data.get("context", {}).get("blockers", []),
            "artifacts_dir": self.artifacts_dir,
            "stages": stages,
        }
