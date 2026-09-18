#include "ratelimit/user_rate_limiter.h"

namespace ratelimit {

UserRateLimiter::UserRateLimiter(std::uint32_t capacity,
                                 std::uint32_t refill_per_tick)
    : capacity_(capacity), refill_per_tick_(refill_per_tick) {}

bool UserRateLimiter::Allow(const std::string& user_key) {
  auto it = buckets_.find(user_key);
  if (it == buckets_.end()) {
    // 首次出现的用户，给一个满额的桶
    it = buckets_.emplace(user_key, TokenBucket(capacity_, refill_per_tick_)).first;
  }
  return it->second.Allow();
}

void UserRateLimiter::Tick() {
  for (auto& entry : buckets_) {
    entry.second.Tick();
  }
}

std::uint32_t UserRateLimiter::Available(const std::string& user_key) const {
  auto it = buckets_.find(user_key);
  if (it == buckets_.end()) {
    // 没用过的用户等同于满额；这里刻意不创建桶，
    // 否则查询这个只读操作就会改变限流状态
    return capacity_;
  }
  return it->second.available();
}

}  // namespace ratelimit
