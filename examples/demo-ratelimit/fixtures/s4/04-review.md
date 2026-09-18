# 代码评审 — 按用户维度限流

## 改动概览

新增 2 个文件（头文件 46 行、实现 34 行），修改 `CMakeLists.txt` 1 行。
纯增量，未触碰既有 `TokenBucket`。改动规模小、边界清晰，整体质量良好。

审查依据：`tasks.yaml` 的 acceptance 与 must_preserve、`impact-report.md` 的影响项、
`02-design.md` 的方案约定。

## 问题清单

### CR-01 🟡 Allow() 中 emplace 会构造一个可能用不上的临时 TokenBucket

- 位置：`src/user_rate_limiter.cpp:13`
- 问题：`buckets_.emplace(user_key, TokenBucket(capacity_, refill_per_tick_))`
  先构造了一个 `TokenBucket` 临时对象再传入。当前 `TokenBucket` 只有三个 `uint32_t`
  成员，拷贝成本可以忽略，但如果后续它增加了重量级成员（比如时间戳队列、互斥量），
  这里会变成热路径上的隐性开销。
- 建议：改用 `piecewise_construct` 或 `try_emplace`（C++17）在原地构造。
  考虑到本工程限定 C++11 且当前开销确实可忽略，也可以接受维持现状但加一行注释说明。

### CR-02 🟡 tracked_users() 属于方案外的新增接口

- 位置：`include/ratelimit/user_rate_limiter.h:39`
- 问题：`02-design.md` 的实现要点里只列了 `Allow` / `Tick` / `Available` 三个方法，
  `tracked_users()` 是实现时新加的。虽然它确实对应了影响报告里「map 无界增长」这条
  中风险项，属于合理补充，但方案外新增公开接口应当在变更记录里显式说明理由。
- 建议：`03-changes.md` 里已经写了「供监控内存增长」，可以接受；建议在头文件注释中
  也补一句它的用途，避免后来者以为是调试遗留物。

### CR-03 🔵 Available() 对未知用户返回满额，语义上可能有歧义

- 位置：`src/user_rate_limiter.cpp:26`
- 问题：未知用户返回 `capacity_`，与「用过但还剩满额」的用户返回值相同，
  调用方无法区分「这个用户从没来过」和「这个用户刚好满额」。
- 建议：本次需求的验收标准明确要求未知用户返回满额，所以实现是对的。
  如果后续监控需要区分两者，再考虑加一个 `IsTracked()`。本条仅作记录，不要求本次处理。

## 评审结论

**可合并**

必修项 0 项，建议项 2 项，可选项 1 项。

核心判断：方案选型（组合而非改造）正确，`TokenBucket` 零改动的承诺兑现了，
`Available()` 的 const 与「不建桶」这两个关键约束都落实到位 —— 这是本次最容易写错
但也最容易被忽视的地方，实现处理得很干净。
