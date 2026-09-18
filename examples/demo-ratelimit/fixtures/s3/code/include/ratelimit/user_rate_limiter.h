// 按用户维度的限流器。
//
// 内部为每个 user_key 持有一个独立的 TokenBucket，用户之间互不挤占配额。
//
// 已知限制（需求阶段已记录在「待澄清」）：
//   - buckets_ 不做淘汰，用户数无上限时会持续增长，调用方需自行控制 key 的基数
//   - 与 TokenBucket 一致，不保证线程安全，并发场景由调用方加锁
#ifndef RATELIMIT_USER_RATE_LIMITER_H_
#define RATELIMIT_USER_RATE_LIMITER_H_

#include <cstdint>
#include <string>
#include <unordered_map>

#include "ratelimit/token_bucket.h"

namespace ratelimit {

class UserRateLimiter {
 public:
  // capacity        每个用户各自的桶容量
  // refill_per_tick 每次 Tick() 给每个用户补充的令牌数
  UserRateLimiter(std::uint32_t capacity, std::uint32_t refill_per_tick);

  // 尝试为指定用户取走一个令牌。首次出现的用户会自动获得一个满额的桶。
  bool Allow(const std::string& user_key);

  // 推进一个时间片，为所有已存在的用户补充令牌。开销与用户数成正比。
  void Tick();

  // 查询指定用户的剩余令牌数。
  // 对从未出现过的用户返回满额容量，且**不会**为其创建桶 ——
  // 查询是只读操作，不能改变限流状态。
  std::uint32_t Available(const std::string& user_key) const;

  // 当前已跟踪的用户数，供监控内存增长使用。
  std::size_t tracked_users() const { return buckets_.size(); }

 private:
  std::uint32_t capacity_;
  std::uint32_t refill_per_tick_;
  std::unordered_map<std::string, TokenBucket> buckets_;
};

}  // namespace ratelimit

#endif  // RATELIMIT_USER_RATE_LIMITER_H_
