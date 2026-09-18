# 技术方案 — 限流器支持按用户维度限流

## 现状分析

### 入口与调用链

`TokenBucket` 是本库唯一对外类型（`include/ratelimit/token_bucket.h:14`）：

```
调用方 → TokenBucket::Allow()  → 判断 tokens_ 是否为 0 → 扣减或拒绝
调用方 → TokenBucket::Tick()   → 补充令牌，补满即止
```

关键实现细节（`src/token_bucket.cpp:16`）：`Tick()` 里做了 `refilled < tokens_` 的回绕判断，
说明作者已经考虑过 `uint32` 溢出。新增实现应保持同等严谨度。

### 现有约束

- 明确不保证线程安全，并发由调用方加锁（头文件注释第 9 行）
- 头文件用 `RATELIMIT_XXX_H_` 形式的 include guard，不用 `#pragma once`
- `CMakeLists.txt:8` 的 `add_library` 显式列出源文件，新增文件必须登记

### 复用判断

`TokenBucket` 语义完整、无隐藏状态、可默认拷贝构造，**适合直接复用为「单个用户的桶」**，
不需要改造。这是本方案成立的前提。

## 方案比选

### 方案 A：给 TokenBucket 加 user_key 参数（改造现有类）

在 `Allow()` 上加一个 `user_key` 参数，类内部维护 map。

- ✅ 不新增类型，对外只有一个限流器
- ❌ 破坏现有接口，所有既有调用方都要改
- ❌ 一个类同时承担「单桶算法」和「多用户路由」两个职责，后续想换算法会很难拆

### 方案 B：新增 UserRateLimiter，内部组合 TokenBucket（推荐）

新增一个类，持有 `unordered_map<string, TokenBucket>`，按 key 路由到对应的桶。

- ✅ `TokenBucket` 零改动，现有行为和调用方完全不受影响
- ✅ 职责清晰：算法在 TokenBucket，多用户路由在 UserRateLimiter
- ✅ 后续要换算法（漏桶、滑动窗口），只要新算法接口一致就能平替
- ❌ 多一个类型，调用方需要知道选哪个

### 方案 C：抽象 RateLimiter 接口，两者都实现它

先抽接口，再让全局限流和按用户限流都实现它。

- ✅ 扩展性最好
- ❌ 当前只有两个实现，抽接口属于过度设计
- ❌ 引入虚函数开销，而限流是热路径

## 推荐方案

**选择方案 B。**

理由：本次需求的本质是「加一种限流粒度」，不是「重构限流体系」。方案 B 用组合而非继承，
以零改动现有代码的代价拿到了新能力，风险最低。方案 C 的接口抽象等到出现第三种限流算法
时再做也不迟 —— 那时候才有足够信息知道接口该长什么样。

### 实现要点

`UserRateLimiter` 的结构（L2 伪代码级描述即可，逻辑不复杂）：

```
class UserRateLimiter {
  构造(capacity, refill_per_tick)     // 记下参数，给新用户建桶时用

  bool Allow(user_key):
      找 user_key 对应的桶，找不到就用 (capacity_, refill_per_tick_) 新建一个
      返回该桶的 Allow()

  void Tick():
      遍历所有桶，各自 Tick()

  uint32 Available(user_key) const:    // const，不能顺手建桶
      找到就返回该桶剩余；找不到说明还没用过，返回满额 capacity_
}
```

`Available()` 必须是 `const` 且对未知用户走只读分支 —— 如果查询顺手把桶建出来，
监控行为本身就改变了限流状态，这是很隐蔽的 bug。

## 影响范围

详见 `impact-report.md`。摘要：

| 维度 | 主要影响 | 等级 |
|---|---|---|
| 代码逻辑 | TokenBucket 零改动 | 低 |
| 状态与时序 | map 随用户数无界增长；Tick 开销随用户数上升 | 中 |
| 功能完整性 | 查询接口必须只读 | 低 |
| 兼容性 | 现有全局限流用法不受影响 | 低 |

## 实施计划

拆分见 `tasks.yaml`，三个任务：

| 任务 | 内容 | 对应需求 |
|---|---|---|
| T1 | 新增 UserRateLimiter（含 Allow）并接入构建 | R-01 |
| T2 | 增加 Available 查询接口 | R-02 |
| TQ | 质量收尾：残留扫描 + 整体构建 | —— |

### 覆盖率矩阵

| 需求 | 承接任务 |
|---|---|
| R-01 按用户独立限流 | T1 |
| R-02 查询剩余令牌 | T2 |

未覆盖需求：无。
