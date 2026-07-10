import time
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from app.config.loader import ConfigLoader
from app.metrics import prometheus

class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, token_bucket):
        super().__init__(app)
        self.token_bucket = token_bucket
        self.config = ConfigLoader()

    async def dispatch(self, request: Request, call_next):
        api_key = request.headers.get("X-API-Key")
        if not api_key:
            prometheus.REQUESTS_TOTAL.labels(status="401").inc()
            return Response(status_code=401, content="Unauthorized")

        path = request.url.path
        method = request.method

        try:
            rule = self.config.get_rule(path, method)
        except ValueError:
            # Use a default rule if not found, or a catch-all
            return await call_next(request)

        try:
            start_time = time.time()
            allowed, remaining = await self.token_bucket.consume(
                api_key, path, rule.capacity, rule.refill_rate
            )
            prometheus.REDIS_LATENCY.observe(time.time() - start_time)

            if not allowed:
                prometheus.REQUESTS_TOTAL.labels(status="429").inc()
                prometheus.BLOCKED_TOTAL.inc()
                retry_after = max(1, int((1 - remaining) / rule.refill_rate))
                reset_time = int(time.time() + (rule.capacity - remaining) / rule.refill_rate)
                return Response(
                    status_code=429,
                    headers={
                        "X-RateLimit-Limit": str(rule.capacity),
                        "X-RateLimit-Remaining": str(int(remaining)),
                        "Retry-After": str(retry_after),
                        "X-RateLimit-Reset": str(reset_time)
                    },
                    content="Too Many Requests"
                )

            prometheus.REQUESTS_TOTAL.labels(status="allowed").inc()
            prometheus.ALLOWED_TOTAL.inc()

            response = await call_next(request)
            reset_time = int(time.time() + (rule.capacity - remaining) / rule.refill_rate)
            response.headers["X-RateLimit-Limit"] = str(rule.capacity)
            response.headers["X-RateLimit-Remaining"] = str(int(remaining))
            response.headers["X-RateLimit-Reset"] = str(reset_time)
            return response

        except Exception as e:
            prometheus.REDIS_ERRORS_TOTAL.inc()
            # FAIL-OPEN
            return await call_next(request)
