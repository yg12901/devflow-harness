// 令牌桶限流器 —— devflow 演示工程的被改造对象。
//
// 当前版本只支持全局限流：整个进程共用一个桶。
// 演示需求会给它加上「按用户维度限流」的能力。
#ifndef RATELIMIT_TOKEN_BUCKET_H_
#define RATELIMIT_TOKEN_BUCKET_H_

#include <cstdint>

namespace ratelimit {

// 固定速率补充的令牌桶。
// 线程安全性：当前实现不保证，调用方需自行加锁。
class TokenBucket {
 public:
  // capacity        桶容量，也是瞬时可承受的最大突发量
  // refill_per_tick 每次 Tick() 补充的令牌数
  TokenBucket(std::uint32_t capacity, std::uint32_t refill_per_tick);

  // 尝试取走一个令牌。取到返回 true（放行），桶空返回 false（限流）。
  bool Allow();

  // 推进一个时间片，补充令牌。补满即止，不会超过 capacity。
  void Tick();

  std::uint32_t available() const { return tokens_; }
  std::uint32_t capacity() const { return capacity_; }

 private:
  std::uint32_t capacity_;
  std::uint32_t refill_per_tick_;
  std::uint32_t tokens_;
};

}  // namespace ratelimit

#endif  // RATELIMIT_TOKEN_BUCKET_H_
