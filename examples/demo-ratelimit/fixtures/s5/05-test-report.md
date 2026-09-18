# 测试报告 — 按用户维度限流

## 用例清单

| 用例 | 覆盖需求 | 类型 | 新增/已有 |
|---|---|---|---|
| `TestUsersDoNotShareQuota` | R-01 | 单元 | 新增 |
| `TestSingleUserRespectsCapacity` | R-01 | 单元 | 新增 |
| `TestTickRefillsAllUsers` | R-01 | 单元 | 新增 |
| `TestAvailableForUnknownUserReturnsCapacity` | R-02 | 单元 | 新增 |
| `TestAvailableReflectsConsumption` | R-02 | 单元 | 新增 |
| `TestAvailableDoesNotCreateBucket` | R-02 | 单元 | 新增 |
| `TestAllowsWithinCapacity` | 回归 | 单元 | 已有 |
| `TestRejectsWhenExhausted` | 回归 | 单元 | 已有 |
| `TestRefillCapsAtCapacity` | 回归 | 单元 | 已有 |

新增用例放在 `tests/test_user_rate_limiter.cpp`，沿用本工程既有的断言式风格
（无框架依赖、失败计数非零则进程返回非零），并在 `CMakeLists.txt` 中注册为
独立的 CTest 目标。

`TestAvailableDoesNotCreateBucket` 是专门为方案和评审都强调过的
「查询不能有副作用」这条约束立的守门用例 —— 这类约束光靠代码 review 守不住，
必须有用例钉死。

## 执行结果

- 命令：`ctest --test-dir build --output-on-failure`
- 结果：**2 个测试目标全部通过**（共 9 条断言，0 失败，0 跳过）
- 输出摘要：
  ```
  Test project build
      Start 1: test_token_bucket
  1/2 Test #1: test_token_bucket ...............   Passed
      Start 2: test_user_rate_limiter
  2/2 Test #2: test_user_rate_limiter ..........   Passed

  100% tests passed, 0 tests failed out of 2
  ```

> 门禁会独立重跑这条命令做二次确认，这里的记录仅供人阅读。

## 验收标准对照

| 需求 | 验收标准 | 覆盖用例 | 结论 |
|---|---|---|---|
| R-01 | 用户 A 耗尽被拒时用户 B 仍放行 | `TestUsersDoNotShareQuota` | ✅ 已验证 |
| R-01 | 同一用户超过容量后被拒绝 | `TestSingleUserRespectsCapacity` | ✅ 已验证 |
| R-02 | 查询未出现过的用户返回桶容量 | `TestAvailableForUnknownUserReturnsCapacity` | ✅ 已验证 |
| R-02 | 查询已消耗令牌的用户返回实际剩余 | `TestAvailableReflectsConsumption` | ✅ 已验证 |

补充覆盖（验收标准未要求但方案/评审关注）：

| 关注点 | 覆盖用例 | 结论 |
|---|---|---|
| 查询是只读的，不创建桶 | `TestAvailableDoesNotCreateBucket` | ✅ 已验证 |
| Tick 为所有用户补充且不超容量 | `TestTickRefillsAllUsers` | ✅ 已验证 |
| 现有全局限流行为无回归 | 已有 3 条用例保持全绿 | ✅ 已验证 |

**未覆盖项**：

| 项 | 原因 |
|---|---|
| 并发安全 | 该类明确声明不保证线程安全，由调用方加锁，不属于本次验收范围 |
| 用户数上限与内存增长 | 需求「待澄清」第 1 条已记录为已知限制，本次不实现淘汰，故无用例 |

以上两项属于**有意识的不覆盖**，已在需求阶段记录，不是遗漏。
