from prometheus_client import Counter, Histogram

REQUESTS_TOTAL = Counter(
    "rate_limiter_requests_total",
    "Total requests",
    ["status"]
)
ALLOWED_TOTAL = Counter(
    "rate_limiter_allowed_total",
    "Total allowed requests"
)
BLOCKED_TOTAL = Counter(
    "rate_limiter_blocked_total",
    "Total blocked requests"
)
REDIS_ERRORS_TOTAL = Counter(
    "rate_limiter_redis_errors_total",
    "Redis connectivity failures"
)
REDIS_LATENCY = Histogram(
    "rate_limiter_redis_latency_seconds",
    "Redis Lua call latency"
)
