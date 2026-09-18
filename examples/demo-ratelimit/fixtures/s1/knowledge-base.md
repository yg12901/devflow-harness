# 项目知识库 — ratelimit_demo

> 本文件贯穿 devflow 全流程，各阶段持续增补。

## 模块地图

| 模块 | 职责 | 位置 |
|---|---|---|
| ratelimit | 限流算法库，对外提供令牌桶实现 | `include/ratelimit/`、`src/` |
| tests | 断言式测试，不依赖第三方测试框架 | `tests/` |

当前只有一个库目标 `ratelimit` 和一个测试目标，构建体系为 CMake + CTest。

## 关键文件索引

| 文件 | 为什么重要 |
|---|---|
| `include/ratelimit/token_bucket.h` | 唯一对外头文件，定义限流器公开契约 |
| `src/token_bucket.cpp` | 令牌桶核心算法，含补充时的溢出保护 |
| `CMakeLists.txt` | 新增源文件与测试都必须在此登记，漏登记会静默不参与构建 |
| `tests/test_token_bucket.cpp` | 测试风格参考：纯断言 + 计数失败，无框架依赖 |

## 设计约定

- `TokenBucket` 明确**不保证线程安全**，并发场景由调用方加锁。
- 令牌补充在 `Tick()` 中完成，补满即止，并防 `uint32` 相加回绕。
- 测试函数命名 `TestXxx`，在 `main()` 里显式调用，失败计数非零则进程返回非零。

## 技术风险与注意事项

- 新增源文件忘记加进 `CMakeLists.txt` 是本工程最容易犯的错误：编译通过但代码根本没被编进去。
- 头文件使用 `RATELIMIT_XXX_H_` 形式的 include guard，不用 `#pragma once`，新增头文件需保持一致。

## 知识更新日志

| 日期 | 阶段 | 更新内容 |
|---|---|---|
| 2026-09-18 | S1 | 建立初版知识库：模块地图、关键文件索引、设计约定 |
