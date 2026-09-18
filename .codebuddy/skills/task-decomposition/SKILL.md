---
name: task-decomposition
description: 把已定方案拆成能独立验证的小任务，写出能过结构校验的 tasks.yaml。S2 拆任务、门禁 CHECK-1 到 CHECK-9 打回后返工时使用。覆盖率用集合运算核对，不采信自己填的「已覆盖」。
---

# 任务拆分

拆任务的目标：developer 拿到清单能一条一条做完，中间不用再猜范围。
结构是否完整由脚本校验，本 skill 教你写出**不会被脚本打回**的清单。

## 一份合格清单长什么样

`tasks.yaml` 必须同时有：`requirements`、`tasks`、`coverage_matrix`。

每个任务带：`id` / `type` / `title` / `depends_on` / `files_to_modify` /
`acceptance` / `completeness_contract` / `status: pending`。

`type`：

- `foundation` —— 接口、公共结构，被后面的任务依赖
- `user_story` —— 真正交付需求点的改动
- `quality_gate` —— 收尾：不写业务代码，只做整体验证（必须有且仅作收尾）

## 动手前先自己算一遍

覆盖率矩阵是声明，门禁不信声明。你自己先做集合差：

1. 每个 `R-*` 是否至少出现在一个任务的 `task_to_requirement` 里
2. 每个非 `quality_gate` 任务是否都能追溯到某个 `R-*`
3. `dependency-map.yaml` 的 `core` 文件是否都被某任务的 `files_to_modify` 认领
4. `associated` 文件是否要么被改、要么出现在某任务的 `must_notify`

`uncovered_requirements` 和 `orphan_tasks` 必须是 `[]`。
填了空数组但集合差算出来非空 —— 这就是自查悖论，脚本会戳穿。

## 粒度与依赖

- 单任务 `files_to_modify` ≤ 5，超了继续拆
- `depends_on` 只指向真实存在的任务 ID，构成的图不能有环
- 先 foundation，再 user_story，最后一条 `quality_gate`
- 所有任务 `status: pending`。写成 `completed` 说明架构师越权写了代码

## 验收条件

每个非 `quality_gate` 任务都要有 `acceptance`：
写成**做完之后能观察到的事实**，不要写成「实现某某功能」。

| 差 | 好 |
|---|---|
| 支持按用户限流 | `Allow(user)` 对未知用户返回满额，已有单用户路径行为不变 |
| 写好测试 | 新增用例覆盖超限拒绝和配额用尽，`test_unit` 退出码 0 |

`quality_gate` 的验收是整体性的：无残留 TODO、无新增死代码、整体构建通过。
