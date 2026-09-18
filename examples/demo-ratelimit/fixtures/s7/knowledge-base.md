# 项目知识库 — ratelimit_demo

> 本文件贯穿 devflow 全流程，各阶段持续增补。

## 模块地图

| 模块 | 职责 | 位置 |
|---|---|---|
| ratelimit | 限流算法库 | `include/ratelimit/`、`src/` |
| ├ TokenBucket | 单桶令牌桶算法，进程级全局限流 | `token_bucket.{h,cpp}` |
| └ UserRateLimiter | 按用户维度限流，内部组合 TokenBucket | `user_rate_limiter.{h,cpp}` |
| tests | 断言式测试，不依赖第三方框架 | `tests/` |

两个限流器是**并列关系**而非继承关系：`UserRateLimiter` 把 `TokenBucket` 当作
「单个用户的桶」来用。需要全局限流就直接用 `TokenBucket`，需要按用户就用 `UserRateLimiter`。

## 关键文件索引

| 文件 | 为什么重要 |
|---|---|
| `include/ratelimit/token_bucket.h` | 基础算法契约，被 UserRateLimiter 复用，改它会波及两个限流器 |
| `src/token_bucket.cpp` | 令牌桶核心算法，`Tick()` 含 uint32 回绕保护 |
| `include/ratelimit/user_rate_limiter.h` | 按用户限流契约；头部注释记录了两条已知限制 |
| `src/user_rate_limiter.cpp` | 按 key 路由到各自的桶；`Available()` 刻意不建桶 |
| `CMakeLists.txt` | 新增源文件与测试都必须在此登记，漏登记会静默不参与构建 |
| `tests/test_user_rate_limiter.cpp` | 含守护「查询无副作用」约束的用例，改查询接口时必看 |

## 设计约定

- 两个限流器都**不保证线程安全**，并发由调用方加锁。
- 令牌补充在 `Tick()` 中完成，补满即止，并防 `uint32` 相加回绕。
- 头文件用 `RATELIMIT_XXX_H_` 形式的 include guard，不用 `#pragma once`。
- 测试函数命名 `TestXxx`，在 `main()` 里显式调用，失败计数非零则返回非零。
- 查询类接口一律 `const`，且不得隐式创建资源。

## 设计权衡记录

| 决策 | 选择 | 代价 | 什么时候该重新评估 |
|---|---|---|---|
| 加按用户限流的方式 | 新增类组合，不改造 TokenBucket | 多一个对外类型 | 出现第三种限流算法时，考虑抽 RateLimiter 接口 |
| 用户桶的淘汰策略 | 不淘汰，暴露 `tracked_users()` 供监控 | 用户数无上限时内存持续增长 | 监控发现 tracked_users 量级异常时 |
| `Allow()` 里的 emplace 临时对象 | 维持现状 | TokenBucket 变重后会成为热路径开销 | TokenBucket 增加非平凡成员时 |
| `Available()` 未知用户返回满额 | 按验收标准返回 capacity | 无法区分「未跟踪」与「恰好满额」 | 监控需要区分两者时，加 `IsTracked()` |

## 技术风险与注意事项

- 新增源文件忘记加进 `CMakeLists.txt` 是本工程最容易犯的错误：编译通过但代码根本没被编进去。
- 给「按 key 分桶」的组件加查询接口时，`operator[]` 会隐式插入元素，
  让只读操作改变状态。必须用 `find()` + const 方法。
- `UserRateLimiter::Tick()` 开销与用户数成正比，用户量大时需要关注调用频率。

## 知识更新日志

| 日期 | 阶段 | 更新内容 |
|---|---|---|
| 2026-09-18 | S1 | 建立初版知识库：模块地图、关键文件索引、设计约定 |
| 2026-09-18 | S2 | 补充复用判断依据：TokenBucket 可默认拷贝，适合作为单用户桶 |
| 2026-09-18 | S7 | 模块地图补充 UserRateLimiter；新增「设计权衡记录」；补充查询接口的风险提示 |
