#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""devflow 引擎单元测试。

    python3 tests/test_engine.py

用标准库 unittest，不需要 pytest。每个用例都在临时目录里跑，不污染源仓库。
"""

import os
import shutil
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEVFLOW_DIR = os.path.join(REPO_ROOT, ".codebuddy", "devflow")
sys.path.insert(0, DEVFLOW_DIR)

from engine import core, gates, learning  # noqa: E402
from engine.state import RunState  # noqa: E402


class TempDevflowCase(unittest.TestCase):
    """把 devflow 的 runs/ 与 learnings/ 重定向到临时目录，保证用例互不干扰。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="devflow-test-")
        self._saved = (core.RUNS_DIR, core.LEARNINGS_DIR, core.EVENTS_DIR,
                       core.ACTIVE_LEARNINGS_FILE)
        core.RUNS_DIR = os.path.join(self.tmp, "runs")
        core.LEARNINGS_DIR = os.path.join(self.tmp, "learnings")
        core.EVENTS_DIR = os.path.join(core.LEARNINGS_DIR, "events")
        core.ACTIVE_LEARNINGS_FILE = os.path.join(core.LEARNINGS_DIR, "active.yaml")
        os.makedirs(core.EVENTS_DIR)

    def tearDown(self):
        (core.RUNS_DIR, core.LEARNINGS_DIR, core.EVENTS_DIR,
         core.ACTIVE_LEARNINGS_FILE) = self._saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, relpath, content):
        path = os.path.join(self.tmp, relpath)
        core.write_text(path, content)
        return path


# ============================================================
# 配置与 profile
# ============================================================

class TestConfig(unittest.TestCase):

    def test_workflow_has_seven_stages(self):
        stages = core.stage_defs(core.load_workflow())
        self.assertEqual([s["id"] for s in stages],
                         ["S1", "S2", "S3", "S4", "S5", "S6", "S7"])

    def test_every_gate_primitive_is_implemented(self):
        """workflow.yaml 不能引用引擎没实现的原语 —— 否则门禁会静默漏检。"""
        unknown = []
        for stage in core.stage_defs(core.load_workflow()):
            for spec in stage.get("gates") or []:
                if spec.get("check") not in gates.PRIMITIVES:
                    unknown.append("%s/%s" % (stage["id"], spec.get("check")))
        self.assertEqual(unknown, [])

    def test_every_stage_has_prompt_and_agent(self):
        workflow = core.load_workflow()
        agents_dir = os.path.join(REPO_ROOT, ".codebuddy", "agents")
        for stage in core.stage_defs(workflow):
            prompt = stage.get("prompt") or ("%s.md" % stage["id"].lower())
            self.assertTrue(os.path.isfile(os.path.join(core.PROMPTS_DIR, prompt)),
                            "缺少 prompt 模板：%s" % prompt)
            role = stage.get("role")
            self.assertTrue(os.path.isfile(os.path.join(agents_dir, role + ".md")),
                            "缺少角色定义：%s" % role)

    def test_default_profile_loads(self):
        profile = core.load_profile("default")
        self.assertTrue(profile["commands"]["build"])
        self.assertTrue(profile["commands"]["test_unit"])

    def test_unknown_profile_raises(self):
        with self.assertRaises(core.ConfigError):
            core.load_profile("no-such-profile")

    def test_render_placeholders(self):
        self.assertEqual(
            core.render("build {{ run_id }} in {{ a.b }}",
                        {"run_id": "R1", "a": {"b": "X"}}),
            "build R1 in X")
        # 未知变量应原样保留，不能静默变成空串
        self.assertEqual(core.render("{{ nope }}", {}), "{{ nope }}")

    def test_run_type_skips(self):
        workflow = core.load_workflow()
        self.assertEqual(core.skipped_stages("feature", workflow), [])
        self.assertIn("S1", core.skipped_stages("hotfix", workflow))


# ============================================================
# 状态机
# ============================================================

class TestState(TempDevflowCase):

    def _new_run(self, run_type="feature"):
        return RunState.create("T-01", "测试需求", run_type, "default",
                               core.load_workflow())

    def test_create_and_load(self):
        self._new_run()
        state = RunState.load("T-01")
        self.assertEqual(state.data["title"], "测试需求")
        self.assertEqual(state.data["current_stage"], "S1")
        self.assertEqual(state.progress(), 0)

    def test_skipped_stages_marked_and_counted(self):
        state = self._new_run("hotfix")
        self.assertEqual(state.stage("S1")["status"], "skipped")
        self.assertEqual(state.next_stage(core.load_workflow())["id"], "S3")

    def test_stage_lifecycle_and_progress(self):
        workflow = core.load_workflow()
        state = self._new_run()
        state.start_stage("S1")
        self.assertEqual(state.stage("S1")["attempts"], 1)
        state.finish_stage("S1", "passed", "搞定", 100)
        self.assertEqual(state.stage("S1")["status"], "passed")
        self.assertEqual(state.data["context"]["last_summary"], "搞定")
        self.assertEqual(state.progress(), 14)  # 1/7
        self.assertEqual(state.next_stage(workflow)["id"], "S2")

    def test_retry_increments_attempts(self):
        state = self._new_run()
        state.start_stage("S1")
        state.finish_stage("S1", "failed", "门禁未过")
        state.start_stage("S1")
        self.assertEqual(state.stage("S1")["attempts"], 2)
        self.assertEqual(state.data["status"], "failed")

    def test_blockers_recorded_and_cleared(self):
        state = self._new_run()
        state.finish_stage("S1", "blocked", "缺依赖")
        self.assertTrue(any(b.startswith("S1:") for b in state.data["context"]["blockers"]))
        state.finish_stage("S1", "passed", "解决了")
        self.assertEqual(state.data["context"]["blockers"], [])

    def test_rollback_clears_downstream_only(self):
        workflow = core.load_workflow()
        state = self._new_run()
        for sid in ("S1", "S2", "S3"):
            state.finish_stage(sid, "passed", "ok", 100)
        cleared = state.rollback("S2", workflow, "方案不可行")
        self.assertEqual(sorted(cleared), ["S2", "S3"])
        self.assertEqual(state.stage("S1")["status"], "passed")   # 上游保留
        self.assertEqual(state.stage("S2")["status"], "pending")
        self.assertEqual(state.data["current_stage"], "S2")

    def test_rollback_rejects_unknown_stage(self):
        state = self._new_run()
        with self.assertRaises(core.ConfigError):
            state.rollback("S99", core.load_workflow())

    def test_completed_when_all_stages_done(self):
        workflow = core.load_workflow()
        state = self._new_run()
        for stage in core.stage_defs(workflow):
            state.finish_stage(stage["id"], "passed", "ok", 100)
        self.assertEqual(state.data["status"], "completed")
        self.assertEqual(state.progress(), 100)
        self.assertIsNone(state.next_stage(workflow))

    def test_load_or_latest_picks_running(self):
        self._new_run()
        other = RunState.create("T-02", "第二条", "feature", "default", core.load_workflow())
        for stage in core.stage_defs(core.load_workflow()):
            other.finish_stage(stage["id"], "passed", "ok", 100)
        self.assertEqual(RunState.load_or_latest().run_id, "T-01")  # 只有 T-01 还在 running


# ============================================================
# 门禁原语
# ============================================================

class TestGates(TempDevflowCase):

    def setUp(self):
        TempDevflowCase.setUp(self)
        self.art = os.path.join(self.tmp, "artifacts")
        os.makedirs(self.art)
        self.profile = core.load_profile("default")

    def run_one(self, spec):
        return gates.run_gates({"gates": [spec]}, self.art, self.profile)

    def test_file_exists_and_nonempty(self):
        core.write_text(os.path.join(self.art, "a.md"), "x" * 100)
        self.assertEqual(self.run_one({"check": "file_exists", "files": ["a.md"]})[0].status, "PASS")
        self.assertEqual(self.run_one({"check": "file_exists", "files": ["missing.md"]})[0].status, "FAIL")
        self.assertEqual(
            self.run_one({"check": "file_nonempty", "files": ["a.md"], "min_chars": 500})[0].status,
            "FAIL")

    def test_sections_present(self):
        core.write_text(os.path.join(self.art, "d.md"), "## 现状分析\n## 方案比选\n")
        self.assertEqual(self.run_one({
            "check": "sections_present", "file": "d.md",
            "all_of": ["现状分析", "方案比选"]})[0].status, "PASS")
        self.assertEqual(self.run_one({
            "check": "sections_present", "file": "d.md",
            "all_of": ["现状分析", "回滚方案"]})[0].status, "FAIL")

    def test_regex_present_counts_unique(self):
        core.write_text(os.path.join(self.art, "r.md"), "R-01 R-01 R-02")
        result = self.run_one({"check": "regex_present", "file": "r.md",
                               "pattern": r"R-\d+", "min_count": 2})[0]
        self.assertEqual(result.status, "PASS")
        result = self.run_one({"check": "regex_present", "file": "r.md",
                               "pattern": r"R-\d+", "min_count": 3})[0]
        self.assertEqual(result.status, "FAIL")  # 去重后只有 2 个

    def test_regex_absent(self):
        core.write_text(os.path.join(self.art, "v.md"), "这里也许有问题")
        self.assertEqual(self.run_one({"check": "regex_absent", "file": "v.md",
                                       "pattern": "(也许|大概齐)"})[0].status, "FAIL")

    def test_yaml_path_empty_and_nonempty(self):
        core.write_text(os.path.join(self.art, "t.yaml"),
                        "coverage_matrix:\n  uncovered_requirements: [R-02]\ntasks: [{id: T1}]\n")
        self.assertEqual(self.run_one({
            "check": "yaml_path_empty", "file": "t.yaml",
            "path": "coverage_matrix.uncovered_requirements"})[0].status, "FAIL")
        self.assertEqual(self.run_one({
            "check": "yaml_path_nonempty", "file": "t.yaml", "path": "tasks"})[0].status, "PASS")

    def test_yaml_items_field_excludes_types(self):
        core.write_text(os.path.join(self.art, "t.yaml"),
                        "tasks:\n"
                        "  - {id: T1, status: completed}\n"
                        "  - {id: T2, status: pending}\n")
        self.assertEqual(self.run_one({
            "check": "yaml_items_field", "file": "t.yaml", "path": "tasks",
            "field": "status", "equals": "completed"})[0].status, "FAIL")

    def test_command_ok_uses_exit_code(self):
        profile = {"_name": "t", "commands": {"yes": "true", "no": "false"}}
        ctx_spec = {"check": "command_ok", "profile_command": "yes"}
        self.assertEqual(gates.run_gates({"gates": [ctx_spec]}, self.art, profile)[0].status, "PASS")
        ctx_spec = {"check": "command_ok", "profile_command": "no"}
        self.assertEqual(gates.run_gates({"gates": [ctx_spec]}, self.art, profile)[0].status, "FAIL")

    def test_command_ok_missing_degrades_to_warn(self):
        profile = {"_name": "t", "commands": {}}
        result = gates.run_gates(
            {"gates": [{"check": "command_ok", "profile_command": "nope", "on_missing": "warn"}]},
            self.art, profile)[0]
        self.assertEqual(result.status, "WARN")
        self.assertFalse(result.blocking)   # WARN 不阻断

    def test_unknown_primitive_fails_loudly(self):
        result = gates.run_gates({"gates": [{"check": "made_up"}]}, self.art, self.profile)[0]
        self.assertEqual(result.status, "FAIL")

    def test_warn_does_not_block_overall(self):
        results = [gates.GateResult("a", "PASS"), gates.GateResult("b", "WARN", severity="warn")]
        summary = gates.summarize(results, "S1")
        self.assertEqual(summary["overall"], "PASS")
        self.assertEqual(summary["score"], 50)


# ============================================================
# 计划结构校验（CHECK-1 .. CHECK-9）
# ============================================================

class TestPlanCheck(TempDevflowCase):

    def setUp(self):
        TempDevflowCase.setUp(self)
        self.art = os.path.join(self.tmp, "artifacts")
        os.makedirs(self.art)

    def write_plan(self, tasks_yaml, depmap_yaml="core: []\nassociated: []\n"):
        core.write_text(os.path.join(self.art, "tasks.yaml"), tasks_yaml)
        core.write_text(os.path.join(self.art, "dependency-map.yaml"), depmap_yaml)

    def check(self):
        return gates.run_plan_check(self.art)

    GOOD = """
requirements:
  - {id: R-01, desc: 甲}
tasks:
  - id: T1
    type: user_story
    depends_on: []
    files_to_modify: [a.cpp]
    acceptance: 能跑
    status: pending
  - id: TQ
    type: quality_gate
    depends_on: [T1]
    files_to_modify: []
    acceptance: 收尾
    status: pending
coverage_matrix:
  requirement_to_task: {R-01: [T1]}
  task_to_requirement: {T1: [R-01]}
  uncovered_requirements: []
  orphan_tasks: []
"""

    def test_good_plan_passes(self):
        self.write_plan(self.GOOD, "core: [a.cpp]\nassociated: []\n")
        self.assertEqual(self.check()["overall"], "PASS")

    def test_uncovered_requirement_detected_despite_false_claim(self):
        """核心用例：架构师漏了 R-02 却声称 uncovered 为空，集合运算要能戳穿。"""
        self.write_plan(self.GOOD.replace(
            "  - {id: R-01, desc: 甲}",
            "  - {id: R-01, desc: 甲}\n  - {id: R-02, desc: 乙}"))
        failed = [c["check"] for c in self.check()["blocking"]]
        self.assertIn("CHECK-1:需求覆盖完整性", failed)

    def test_orphan_task_detected(self):
        plan = self.GOOD.replace("  task_to_requirement: {T1: [R-01]}",
                                 "  task_to_requirement: {}")
        self.write_plan(plan, "core: [a.cpp]\nassociated: []\n")
        self.assertIn("CHECK-2:无孤立任务", [c["check"] for c in self.check()["blocking"]])

    def test_uncovered_core_file_detected(self):
        self.write_plan(self.GOOD, "core: [a.cpp, forgotten.cpp]\nassociated: []\n")
        self.assertIn("CHECK-3:核心层文件全覆盖",
                      [c["check"] for c in self.check()["blocking"]])

    def test_associated_file_needs_modify_or_notify(self):
        self.write_plan(self.GOOD, "core: [a.cpp]\nassociated: [caller.cpp]\n")
        self.assertIn("CHECK-4:关联层文件已知会",
                      [c["check"] for c in self.check()["blocking"]])

    def test_associated_satisfied_by_must_notify(self):
        plan = self.GOOD.replace(
            "    acceptance: 能跑\n",
            "    acceptance: 能跑\n"
            "    completeness_contract:\n"
            "      must_notify:\n"
            "        - {file: caller.cpp, reason: 调用方}\n")
        self.write_plan(plan, "core: [a.cpp]\nassociated: [caller.cpp]\n")
        self.assertEqual(self.check()["overall"], "PASS")

    def test_dependency_cycle_detected(self):
        plan = """
requirements: [{id: R-01, desc: 甲}]
tasks:
  - {id: T1, type: user_story, depends_on: [T2], files_to_modify: [a], acceptance: x, status: pending}
  - {id: T2, type: user_story, depends_on: [T1], files_to_modify: [b], acceptance: y, status: pending}
  - {id: TQ, type: quality_gate, depends_on: [], files_to_modify: [], acceptance: z, status: pending}
coverage_matrix:
  requirement_to_task: {R-01: [T1, T2]}
  task_to_requirement: {T1: [R-01], T2: [R-01]}
  uncovered_requirements: []
  orphan_tasks: []
"""
        self.write_plan(plan)
        self.assertIn("CHECK-5:依赖图无环", [c["check"] for c in self.check()["blocking"]])

    def test_dangling_dependency_detected(self):
        plan = self.GOOD.replace("    depends_on: []", "    depends_on: [T99]", 1)
        self.write_plan(plan, "core: [a.cpp]\nassociated: []\n")
        self.assertIn("CHECK-6:依赖指向有效任务",
                      [c["check"] for c in self.check()["blocking"]])

    def test_missing_quality_gate_task_detected(self):
        plan = self.GOOD.split("  - id: TQ")[0] + """
coverage_matrix:
  requirement_to_task: {R-01: [T1]}
  task_to_requirement: {T1: [R-01]}
  uncovered_requirements: []
  orphan_tasks: []
"""
        self.write_plan(plan, "core: [a.cpp]\nassociated: []\n")
        self.assertIn("CHECK-9:含质量门禁任务",
                      [c["check"] for c in self.check()["blocking"]])

    def test_oversized_task_warns_but_does_not_block(self):
        plan = self.GOOD.replace("    files_to_modify: [a.cpp]",
                                 "    files_to_modify: [a,b,c,d,e,f,g]")
        self.write_plan(plan)
        summary = self.check()
        names = [c["check"] for c in summary["checks"] if c["status"] == "FAIL"]
        self.assertIn("CHECK-7:任务粒度可控", names)
        self.assertNotIn("CHECK-7:任务粒度可控", [c["check"] for c in summary["blocking"]])

    def test_missing_tasks_file_fails_gracefully(self):
        self.assertEqual(gates.run_plan_check(self.art)["overall"], "FAIL")


# ============================================================
# 自学习闭环
# ============================================================

class TestLearning(TempDevflowCase):

    def setUp(self):
        TempDevflowCase.setUp(self)
        learning.save_entries([])

    def gate_fail(self, stage, check, evidence="证据"):
        return {"blocking": [{"check": check, "evidence": evidence, "message": "对策"}]}

    def test_yaml_roundtrip_with_star_stage(self):
        """stage 取 '*' 时必须加引号，否则 YAML 会当成别名解析 —— 这是真实踩过的坑。"""
        learning.save_entries([{
            "id": "L-001", "priority": "P0", "status": "active", "stage": "*",
            "signature": "s", "pattern": "含: 冒号 与 #井号", "fix": "对策",
            "tags": ["a-b", "*weird"], "origin": "manual",
            "created": "2026-01-01", "updated": "2026-01-01",
            "stats": {"hits": 1, "runs": 1, "injected": 0, "fail_after_inject": 0,
                      "effective_streak": 0, "proven": False, "run_ids": ["R1"], "stages": ["S1"]},
        }])
        back = learning.load_entries()
        self.assertEqual(back[0]["stage"], "*")
        self.assertEqual(back[0]["pattern"], "含: 冒号 与 #井号")
        self.assertEqual(back[0]["tags"], ["a-b", "*weird"])

    def test_gate_failure_auto_records_event(self):
        learning.record_gate_failures("R1", "S2", self.gate_fail("S2", "CHECK-1"))
        events = learning.load_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["signature"], "gate_fail:S2:CHECK-1")

    def test_distill_requires_min_hits(self):
        learning.record_gate_failures("R1", "S2", self.gate_fail("S2", "CHECK-1"))
        self.assertEqual(learning.distill()["created"], [])   # 只命中 1 次
        learning.record_gate_failures("R2", "S2", self.gate_fail("S2", "CHECK-1"))
        self.assertEqual(len(learning.distill()["created"]), 1)

    def test_promotion_p2_to_p1_across_runs(self):
        for run in ("R1", "R2"):
            learning.record_gate_failures(run, "S2", self.gate_fail("S2", "CHECK-1"))
        learning.distill()
        entry = learning.load_entries()[0]
        self.assertEqual(entry["priority"], "P1")
        self.assertEqual(entry["stats"]["runs"], 2)

    def test_promotion_to_p0_needs_multiple_stages(self):
        for run, stage in (("R1", "S2"), ("R2", "S2"), ("R3", "S3")):
            learning.record_gate_failures(run, stage, self.gate_fail(stage, "CHECK-X"))
        # 三个 run 但签名按阶段区分，所以先看单签名不会升 P0
        learning.distill()
        s2 = [e for e in learning.load_entries() if e["signature"] == "gate_fail:S2:CHECK-X"]
        self.assertEqual(s2[0]["priority"], "P1")

    def test_query_respects_priority_and_stage(self):
        learning.save_entries([
            {"id": "L-P0", "priority": "P0", "status": "active", "stage": "*",
             "signature": "a", "pattern": "全局", "fix": "", "tags": [], "origin": "manual",
             "created": "x", "updated": "x", "stats": {"hits": 0, "runs": 0, "injected": 0,
             "fail_after_inject": 0, "effective_streak": 0, "proven": False,
             "run_ids": [], "stages": []}},
            {"id": "L-S2", "priority": "P1", "status": "active", "stage": "S2",
             "signature": "b", "pattern": "仅 S2", "fix": "", "tags": [], "origin": "auto",
             "created": "x", "updated": "x", "stats": {"hits": 0, "runs": 0, "injected": 0,
             "fail_after_inject": 0, "effective_streak": 0, "proven": False,
             "run_ids": [], "stages": []}},
        ])
        self.assertEqual([e["id"] for e in learning.query("S2")], ["L-P0", "L-S2"])
        self.assertEqual([e["id"] for e in learning.query("S5")], ["L-P0"])

    def test_retired_never_injected(self):
        learning.save_entries([
            {"id": "L-X", "priority": "P0", "status": "retired", "stage": "*",
             "signature": "a", "pattern": "已淘汰", "fix": "", "tags": [], "origin": "auto",
             "created": "x", "updated": "x", "stats": {"hits": 0, "runs": 0, "injected": 0,
             "fail_after_inject": 0, "effective_streak": 0, "proven": False,
             "run_ids": [], "stages": []}},
        ])
        self.assertEqual(learning.query("S1"), [])

    def _seed_injected_entry(self, run_id="R1", stage="S2", check="CHECK-1"):
        signature = "gate_fail:%s:%s" % (stage, check)
        learning.save_entries([{
            "id": "L-001", "priority": "P1", "status": "active", "stage": stage,
            "signature": signature, "pattern": "p", "fix": "f", "tags": [], "origin": "auto",
            "created": "x", "updated": "x",
            "stats": {"hits": 2, "runs": 2, "injected": 0, "fail_after_inject": 0,
                      "effective_streak": 0, "proven": False, "run_ids": [], "stages": []},
        }])
        learning.record_injection(run_id, stage, learning.load_entries())
        return signature

    def test_feedback_demotes_ineffective_learning(self):
        """注入之后同一门禁还是失败 —— 说明这条经验没用，要降级并标记待改写。"""
        self._seed_injected_entry()
        for _ in range(2):
            learning.feedback("R1", "S2", self.gate_fail("S2", "CHECK-1"))
        entry = learning.load_entries()[0]
        self.assertEqual(entry["status"], learning.STATUS_NEEDS_REWRITE)
        self.assertEqual(entry["priority"], "P2")
        self.assertEqual(entry["stats"]["fail_after_inject"], 2)

    def test_feedback_retires_after_repeated_failure(self):
        self._seed_injected_entry()
        for _ in range(4):
            learning.feedback("R1", "S2", self.gate_fail("S2", "CHECK-1"))
        self.assertEqual(learning.load_entries()[0]["status"], learning.STATUS_RETIRED)

    def test_feedback_proves_effective_learning(self):
        """注入之后连续通过 —— 固化为 proven，不再参与降级。"""
        self._seed_injected_entry()
        for _ in range(3):
            learning.feedback("R1", "S2", {"blocking": []})
        entry = learning.load_entries()[0]
        self.assertTrue(entry["stats"]["proven"])
        self.assertEqual(entry["stats"]["effective_streak"], 3)

    def test_auto_signature_separates_distinct_problems(self):
        """同阶段的不同问题不能聚成一簇，否则蒸馏出来是糊状混合体。"""
        learning.record_event("R1", "S3", "pitfall", "问题甲")
        learning.record_event("R1", "S3", "pitfall", "问题乙")
        signatures = set(e["signature"] for e in learning.load_events())
        self.assertEqual(len(signatures), 2)

    def test_format_for_prompt_marks_status(self):
        learning.save_entries([{
            "id": "L-001", "priority": "P0", "status": "active", "stage": "*",
            "signature": "a", "pattern": "模式", "fix": "对策", "tags": [],
            "origin": "manual", "created": "x", "updated": "x",
            "stats": {"hits": 0, "runs": 0, "injected": 0, "fail_after_inject": 0,
                      "effective_streak": 0, "proven": True, "run_ids": [], "stages": []},
        }])
        text = learning.format_for_prompt(learning.query("S1"))
        self.assertIn("已验证", text)
        self.assertIn("对策：对策", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
