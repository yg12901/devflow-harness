#include "ratelimit/token_bucket.h"

namespace ratelimit {

TokenBucket::TokenBucket(std::uint32_t capacity, std::uint32_t refill_per_tick)
    : capacity_(capacity), refill_per_tick_(refill_per_tick), tokens_(capacity) {}

bool TokenBucket::Allow() {
  if (tokens_ == 0) {
    return false;
  }
  --tokens_;
  return true;
}

void TokenBucket::Tick() {
  std::uint32_t refilled = tokens_ + refill_per_tick_;
  // 补满即止；同时防止 uint32 相加溢出回绕
  tokens_ = (refilled > capacity_ || refilled < tokens_) ? capacity_ : refilled;
}

}  // namespace ratelimit
