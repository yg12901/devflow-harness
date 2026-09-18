cmake_minimum_required(VERSION 3.10)
project(ratelimit_demo CXX)

set(CMAKE_CXX_STANDARD 11)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

add_library(ratelimit
  src/token_bucket.cpp
  src/user_rate_limiter.cpp
)
target_include_directories(ratelimit PUBLIC include)

enable_testing()

add_executable(test_token_bucket tests/test_token_bucket.cpp)
target_link_libraries(test_token_bucket PRIVATE ratelimit)
add_test(NAME test_token_bucket COMMAND test_token_bucket)
