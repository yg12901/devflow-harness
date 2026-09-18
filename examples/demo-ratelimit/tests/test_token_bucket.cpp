// 极简断言式测试，不依赖任何测试框架 —— 演示工程要能在任何机器上直接跑起来。
#include "ratelimit/token_bucket.h"

#include <cstdio>
#include <cstdlib>

namespace {

int g_failures = 0;

void Check(bool condition, const char* name) {
  if (condition) {
    std::printf("  [PASS] %s\n", name);
  } else {
    std::printf("  [FAIL] %s\n", name);
    ++g_failures;
  }
}

// R-01 覆盖：桶内有令牌时放行
void TestAllowsWithinCapacity() {
  ratelimit::TokenBucket bucket(3, 1);
  Check(bucket.Allow() && bucket.Allow() && bucket.Allow(),
        "TestAllowsWithinCapacity: 容量内的请求全部放行");
}

// R-01 覆盖：令牌耗尽后拒绝
void TestRejectsWhenExhausted() {
  ratelimit::TokenBucket bucket(2, 1);
  bucket.Allow();
  bucket.Allow();
  Check(!bucket.Allow(), "TestRejectsWhenExhausted: 令牌耗尽后拒绝");
}

// R-01 覆盖：Tick 补充令牌且不超过容量
void TestRefillCapsAtCapacity() {
  ratelimit::TokenBucket bucket(2, 5);
  bucket.Allow();
  bucket.Allow();
  bucket.Tick();
  Check(bucket.available() == 2, "TestRefillCapsAtCapacity: 补充不超过容量上限");
}

}  // namespace

int main() {
  std::printf("running token_bucket tests\n");
  TestAllowsWithinCapacity();
  TestRejectsWhenExhausted();
  TestRefillCapsAtCapacity();
  if (g_failures == 0) {
    std::printf("all tests passed\n");
    return EXIT_SUCCESS;
  }
  std::printf("%d test(s) failed\n", g_failures);
  return EXIT_FAILURE;
}
