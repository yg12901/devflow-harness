# 代码变更记录 — 按用户维度限流

## 任务执行记录

### T1 新增 UserRateLimiter，复用 TokenBucket 实现按用户限流

- 改动：
  - `include/ratelimit/user_rate_limiter.h`（新增，46 行）—— 定义 `UserRateLimiter`，
    持有 `unordered_map<string, TokenBucket>`；头部注释显式写明两条已知限制（无淘汰、非线程安全）
  - `src/user_rate_limiter.cpp`（新增，34 行）—— `Allow()` 按 key 查桶，未命中则新建满额桶
  - `CMakeLists.txt:9` —— 在 `add_library(ratelimit ...)` 列表里加入 `src/user_rate_limiter.cpp`

- 验证（对照 T1 的 acceptance）：
  - 不同 user_key 各持独立桶 —— `Allow()` 用 `buckets_.find(user_key)` 路由，不同 key 命中不同 entry ✅
  - 一个用户耗尽不影响其他用户 —— 令牌状态在各自的 `TokenBucket` 实例内，无共享变量 ✅
  - 新增源文件已登记且构建通过 —— 见下方「构建验证」 ✅

- `must_notify` 检查：
  - `include/ratelimit/token_bucket.h` —— **已检查**。`TokenBucket` 无自定义拷贝构造、
    无自定义析构、成员全是 `uint32_t`，默认拷贝语义正确，可安全放进 `unordered_map`。
    该文件本次**零改动**（`git diff` 为空），现有全局限流行为不受影响。

### T2 增加 Available(user_key) 查询接口

- 改动：
  - `include/ratelimit/user_rate_limiter.h:36` —— 新增 `Available(const std::string&) const`
  - `include/ratelimit/user_rate_limiter.h:39` —— 顺带新增 `tracked_users()`，
    暴露 map 大小供监控内存增长（对应影响报告里的中风险项）
  - `src/user_rate_limiter.cpp:23` —— 实现：命中返回 `available()`，未命中返回 `capacity_`

- 验证（对照 T2 的 acceptance）：
  - 未出现过的用户返回桶容量 —— 未命中分支直接 `return capacity_` ✅
  - 已消耗令牌的用户返回实际剩余 —— 命中分支转发 `TokenBucket::available()` ✅
  - 查询不消耗令牌、不改状态 —— 方法声明为 `const`，未命中分支**刻意不创建桶**，
    编译器会强制保证无副作用 ✅

### TQ 质量收尾

- TODO 残留扫描：`grep -rn "TODO\|FIXME" include/ src/` → 0 处命中 ✅
- 注释掉的死代码扫描：人工过了两个新增文件，无注释掉的代码块 ✅
- 整体构建：见下方 ✅

## 构建验证

- 命令：`cmake -S . -B build -DCMAKE_BUILD_TYPE=Debug && cmake --build build -j 4`
- 结果：**成功**
- 输出摘要：
  ```
  [ 40%] Building CXX object CMakeFiles/ratelimit.dir/src/token_bucket.cpp.o
  [ 60%] Building CXX object CMakeFiles/ratelimit.dir/src/user_rate_limiter.cpp.o
  [ 80%] Linking CXX static library libratelimit.a
  [100%] Built target test_token_bucket
  ```

> 门禁会独立重跑这条命令做二次确认，这里的记录仅供人阅读。

## 影响范围确认

逐条对照 `impact-report.md`：

| 影响项 | 检查方式 | 结论 |
|---|---|---|
| TokenBucket 被复用但不修改 | `git diff include/ratelimit/token_bucket.h src/token_bucket.cpp` | 无输出，确认零改动 ✅ |
| 新增公开头文件无既有调用方 | 改动前 `grep -rn "UserRateLimiter" .` | 0 处命中，纯新增 ✅ |
| map 无界增长 | 已在头文件注释显式声明该限制 | 已处理：并新增 `tracked_users()` 供监控 ✅ |
| Tick 开销随用户数上升 | 审查实现 | 单层 range-for，O(n) 无嵌套 ✅ |
| 查询接口必须只读 | 审查签名与实现 | 声明为 `const`，未命中分支不建桶 ✅ |
| 现有全局限流用法不受影响 | 已有测试 `test_token_bucket` | 保持全绿 ✅ |

无未处理项。
