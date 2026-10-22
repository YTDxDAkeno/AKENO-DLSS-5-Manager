// AKENO diagnostic additions, GPL-3.0-or-later. Corresponding patch source is distributed.
// Counters observe the backend's own existing NR calls. No injection or runtime modifications.
#pragma once
#include <atomic>
#include <cstdint>
#include <utility>
#ifdef _WIN32
#include <Windows.h>
#endif
namespace AkenoNrDiagnostic {
inline constexpr const char* kBuild = "akeno-fc702f8-v1";
struct Counters {
    std::atomic<uint64_t> create{0}, created{0}, createFailed{0};
    std::atomic<uint64_t> attempts{0}, success{0}, failed{0}, sequence{0}, lastTick{0};
};
inline Counters counters;
inline void Increment(std::atomic<uint64_t>& value) {
    auto current = value.load(std::memory_order_relaxed);
    while (current != UINT64_MAX && !value.compare_exchange_weak(current, current + 1, std::memory_order_relaxed)) {}
}
inline uint64_t Birth() {
#ifdef _WIN32
    static const uint64_t birth = [] { FILETIME c{}, e{}, k{}, u{};
        return GetProcessTimes(GetCurrentProcess(), &c, &e, &k, &u)
            ? (uint64_t(c.dwHighDateTime) << 32) | c.dwLowDateTime : uint64_t(0); }();
    return birth;
#else
    return 0; // Portable compile tests have no Windows/GPU evidence.
#endif
}
inline void Summary(unsigned result) {
#ifdef _WIN32
    const uint64_t tick = GetTickCount64();
    auto previous = counters.lastTick.load(std::memory_order_relaxed);
    if (previous && tick - previous < 5000) return;
    if (!counters.lastTick.compare_exchange_strong(previous, tick, std::memory_order_relaxed)) return;
    Increment(counters.sequence);
    LOG_INFO("AKENO_NR_V1 build={} pid={} birth={} seq={} create_attempts={} create_success={} create_fail={} eval_attempts={} eval_success={} eval_fail={} last_result=0x{:08X}",
        kBuild, GetCurrentProcessId(), Birth(), counters.sequence.load(), counters.create.load(), counters.created.load(),
        counters.createFailed.load(), counters.attempts.load(), counters.success.load(), counters.failed.load(), result);
#endif
}
inline void RecordEvaluate(unsigned result) {
    Increment(counters.attempts);
    Increment(result == 1 ? counters.success : counters.failed);
    Summary(result);
}
inline void RecordCreate(bool success) {
    Increment(counters.create);
    Increment(success ? counters.created : counters.createFailed);
    Summary(success ? 1u : 0u);
}
template<class Call> auto Evaluate(Call&& call) {
    auto result = std::forward<Call>(call)();
    RecordEvaluate(static_cast<unsigned>(result));
    return result;
}
template<class Call> auto Create(Call&& call) {
    auto result = std::forward<Call>(call)();
    RecordCreate(result != nullptr);
    return result;
}
}
