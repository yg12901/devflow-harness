// 按用户维度限流的测试，沿用本工程的断言式风格（无框架依赖）。
#include "ratelimit/user_rate_limiter.h"

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

// R-01 覆盖：一个用户耗尽令牌被拒绝时，另一个用户不受影响
void TestUsersDoNotShareQuota() {
  ratelimit::UserRateLimiter limiter(2, 1);
  Check(limiter.Allow("alice"), "alice 第 1 次放行");
  Check(limiter.Allow("alice"), "alice 第 2 次放行");
  Check(!limiter.Allow("alice"), "alice 第 3 次被拒绝（已耗尽）");
  Check(limiter.Allow("bob"),
        "TestUsersDoNotShareQuota: alice 耗尽后 bob 仍能放行");
}

// R-01 覆盖：同一用户连续请求超过容量后被拒绝
void TestSingleUserRespectsCapacity() {
  ratelimit::UserRateLimiter limiter(3, 1);
  limiter.Allow("carol");
  limiter.Allow("carol");
  limiter.Allow("carol");
  Check(!limiter.Allow("carol"),
        "TestSingleUserRespectsCapacity: 超过容量后拒绝");
}

// R-02 覆盖：查询从未出现过的用户，返回满额
void TestAvailableForUnknownUserReturnsCapacity() {
  ratelimit::UserRateLimiter limiter(5, 1);
  Check(limiter.Available("never-seen") == 5,
        "TestAvailableForUnknownUserReturnsCapacity: 未知用户返回桶容量");
}

// R-02 覆盖：查询已消耗令牌的用户，返回实际剩余数
void TestAvailableReflectsConsumption() {
  ratelimit::UserRateLimiter limiter(5, 1);
  limiter.Allow("dave");
  limiter.Allow("dave");
  Check(limiter.Available("dave") == 3,
        "TestAvailableReflectsConsumption: 消耗 2 个后剩余 3 个");
}

// R-02 覆盖：查询是只读的，不能顺手为未知用户创建桶
// 这是评审和方案都特别强调的约束，单独立一条用例守住它
void TestAvailableDoesNotCreateBucket() {
  ratelimit::UserRateLimiter limiter(5, 1);
  limiter.Available("ghost");
  Check(limiter.tracked_users() == 0,
        "TestAvailableDoesNotCreateBucket: 查询未知用户不创建桶");
}

// R-01 覆盖：Tick 为所有已存在用户补充令牌，且不超过容量
void TestTickRefillsAllUsers() {
  ratelimit::UserRateLimiter limiter(2, 5);
  limiter.Allow("eve");
  limiter.Allow("frank");
  limiter.Tick();
  Check(limiter.Available("eve") == 2 && limiter.Available("frank") == 2,
        "TestTickRefillsAllUsers: 所有用户补满且不超过容量");
}

}  // namespace

int main() {
  std::printf("running user_rate_limiter tests\n");
  TestUsersDoNotShareQuota();
  TestSingleUserRespectsCapacity();
  TestAvailableForUnknownUserReturnsCapacity();
  TestAvailableReflectsConsumption();
  TestAvailableDoesNotCreateBucket();
  TestTickRefillsAllUsers();
  if (g_failures == 0) {
    std::printf("all tests passed\n");
    return EXIT_SUCCESS;
  }
  std::printf("%d test(s) failed\n", g_failures);
  return EXIT_FAILURE;
}
