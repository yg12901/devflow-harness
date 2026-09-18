恢复之前中断的 devflow 流水线，从断点继续往下跑。

## 执行

```bash
DF=.codebuddy/devflow/bin/devflow
$DF resume            # 不带 --run-id 会自动选最近一个 running 的运行
```

返回里包含：

- `completed_stages` —— 已完成的阶段及其摘要（**这些不要重做**）
- `context` —— 需求摘要、技术决策、关键文件、当前阻塞项
- `artifact_inspection` —— 断点阶段产物的真实情况
- `prompt` —— 下一阶段的完整 prompt，可直接投喂

## 先看产物真实进度，再决定怎么续

`artifact_inspection.verdict` 决定续跑方式：

| verdict | 含义 | 怎么做 |
|---|---|---|
| `complete` | 产物齐备，只是没来得及上报 | **直接跑门禁推进**，不要重跑 subagent（重跑会覆盖已完成的工作） |
| `partial` | 做了一半 | 带着已完成部分重新调用，让它只补缺失的产物 |
| `empty` | 什么都没产出 | 提高 max_turns 重新调用 |

这是中断恢复的核心：**不看产物就重跑，是把已完成的工作又推翻一遍**。

## 然后继续主循环

按 `.codebuddy/agents/leader.md` 的主循环继续，直到全部完成。

---

## 恢复错了运行怎么办

```bash
$DF list                        # 看所有运行
$DF resume --run-id <run_id>    # 指定恢复哪一个
```

补充说明：$ARGUMENTS
