查看 devflow 流水线当前状态。只读，不改任何状态。

## 执行

```bash
DF=.codebuddy/devflow/bin/devflow
$DF list      # 所有运行的概览
$DF status    # 当前（最近 running 的）运行详情
```

指定某个运行：`$DF status --run-id <run_id>`

## 整理成人看的格式

把 JSON 转成这样汇报，不要直接把 JSON 甩给用户：

```
需求 REQ-20260918-01 · 给限流器加用户维度配额
类型 feature · 技术栈 default · 进度 57%

  ✅ S1 需求分析          通过 100 分   12s
  ✅ S2 方案与拆分        通过  96 分   48s   （重试 1 次）
  ✅ S3 编码实现          通过 100 分  2m14s
  ✅ S4 代码评审          通过  92 分   55s
  ▶  S5 测试验证          进行中
  ⏸  S6 发布              待开始
  ⏸  S7 知识沉淀          待开始

当前阻塞：无
经验库：12 条（P0 3 / P1 5 / P2 4），其中 3 条已验证
产物目录：.codebuddy/devflow/runs/REQ-20260918-01/artifacts
```

状态含义：`pending` 待开始 / `running` 进行中 / `passed` 通过 / `failed` 门禁未过 / `blocked` 需人工介入 / `skipped` 按需求类型跳过。

有 `blockers` 时要明确列出来，并给出建议动作（重试、回退、还是需要人工决策）。

补充说明：$ARGUMENTS
