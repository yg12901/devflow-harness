# 发布单 — 按用户维度限流

> 本次以 `release.dry_run: true` 运行：只生成发布单，不触发真实发布流水线。

## 变更内容

| 项 | 内容 |
|---|---|
| 对应需求 | R-01 按用户维度独立限流、R-02 查询用户剩余令牌 |
| 新增文件 | `include/ratelimit/user_rate_limiter.h`、`src/user_rate_limiter.cpp` |
| 修改文件 | `CMakeLists.txt`（登记新增源文件与测试目标）、`tests/test_user_rate_limiter.cpp`（新增） |
| 未改动 | `include/ratelimit/token_bucket.h`、`src/token_bucket.cpp` —— 现有全局限流零影响 |
| 影响面 | 纯增量。既有调用方无需任何改动，不需要同步升级 |

对外接口新增（无删除、无签名变更）：

```
ratelimit::UserRateLimiter(capacity, refill_per_tick)
  bool   Allow(user_key)
  void   Tick()
  uint32 Available(user_key) const
  size_t tracked_users() const
```

## 发布方式

本次为库代码变更，随调用方下次发版一起生效，**无需独立发布动作**。

步骤：
1. 合并请求合入主干（本次 dry-run，未自动创建 MR；需手工在代码平台创建，
   标题建议：`ratelimit: 支持按用户维度限流 (R-01/R-02)`）
2. 调用方在需要按用户限流时显式改用 `UserRateLimiter`，不改则行为完全不变
3. 由于是纯新增接口，**不需要灰度**：没有调用方就没有行为变化

## 验证方式

合入后确认：

1. 主干 CI 的构建与测试保持全绿（关注 `test_token_bucket` 与 `test_user_rate_limiter` 两个目标）
2. 接入方首次使用时，观察 `tracked_users()` 的量级是否符合预期 ——
   这是「map 无界增长」这条中风险项的观测口子，如果量级远超预期说明 user_key 选错了维度
3. 对照 R-01：构造两个 key 分别打满，确认互不影响
4. 对照 R-02：对未使用过的 key 查询，应返回桶容量

## 回滚方案

本次为纯新增，回滚成本极低。

**方式一（推荐）：代码回滚**
1. `git revert <合并提交的 hash>`
2. 重新构建：`cmake -S . -B build && cmake --build build -j 4`
3. 确认 `ctest --test-dir build` 中 `test_token_bucket` 通过
   （`test_user_rate_limiter` 会随回滚一起消失，属预期）
4. 生效时间：随下次发版

**方式二：调用方降级（无需回滚代码）**
若问题出在接入方而非库本身，接入方改回使用原来的 `TokenBucket` 即可。
新增代码留在库里不会产生任何运行时影响（没有调用就不会执行）。
这也是选择「新增类而非改造原类」这个方案带来的直接收益。

**判断用哪种**：库本身有 bug 用方式一；接入姿势有问题用方式二。
