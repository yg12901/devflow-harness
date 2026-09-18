# 评审响应 — 按用户维度限流

逐条对照 `04-review.md` 的问题清单，无遗漏。

### CR-01 Allow() 中 emplace 构造临时对象

**决策：拒绝（附技术理由）**

理由：
1. 当前 `TokenBucket` 只有三个 `uint32_t` 成员（`token_bucket.h:31-33`），
   拷贝成本是 12 字节的平凡拷贝，编译器在 -O2 下基本会消除。
2. 本工程 `CMakeLists.txt:4` 锁定 `CMAKE_CXX_STANDARD 11`，`try_emplace` 是 C++17，
   不可用；`piecewise_construct` 写法可用但会显著降低这段代码的可读性。
3. 评审意见里的顾虑成立条件是「TokenBucket 将来变重」。真到那一天，
   这里会和其他所有按值持有 TokenBucket 的地方一起需要重新评估，
   届时统一处理比现在单点优化更合适。

已采纳评审的替代建议：在 `src/user_rate_limiter.cpp:12` 加了一行注释，
说明此处的拷贝开销前提，为将来的人留下判断依据。

### CR-02 tracked_users() 属于方案外新增

**决策：采纳**

改动：`include/ratelimit/user_rate_limiter.h:38` 补充注释
「当前已跟踪的用户数，供监控内存增长使用」，明确它不是调试遗留物，
而是对应 `impact-report.md` 中「map 无界增长」这条中风险项的可观测性补充。

验证：构建通过。

### CR-03 Available() 对未知用户的语义歧义

**决策：后续迭代**

理由：本次需求 R-02 的验收标准明确写的是「对从未出现过的用户查询，返回满额」，
当前实现符合验收标准。区分「未跟踪」与「满额」属于新的监控诉求，超出本次范围。

已在 `07-knowledge.md` 中记录该设计权衡，供后续需求参考。

---

## 响应后重新构建

- 命令：`cmake -S . -B build -DCMAKE_BUILD_TYPE=Debug && cmake --build build -j 4`
- 结果：**成功**（本轮仅改注释，无逻辑变更）
