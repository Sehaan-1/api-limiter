# Distributed API Rate Limiter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a production-grade, distributed API rate limiter using FastAPI and Redis Cluster with a Token Bucket algorithm.

**Architecture:** Shared-state middleware pattern where FastAPI instances call a Redis Lua script for atomic token consumption.

**Tech Stack:** Python 3.11, FastAPI, Redis (Cluster), Lua, Go, Prometheus, Grafana, Docker Compose.

## Global Constraints
- **Latency:** < 2ms overhead per request (Redis round-trip).
- **Correctness:** Zero double-spend under concurrent load (Lua atomicity).
- **Availability:** Fail-open on Redis outage.
- **Observability:** Prometheus metrics at `/metrics`.
- **Config:** YAML file at `config/rate_limits.yaml` with hot-reload.
- **Headers:** `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset`, `Retry-After`.

---

## File Mapping
- `app/main.py`: FastAPI entry point, routes, and middleware attachment.
- `app/config/loader.py`: YAML config loader and file watcher.
- `app/limiter/token_bucket.py`: Interface for Redis interaction.
- `app/limiter/lua_scripts/token_bucket.lua`: Atomic Lua script for token consumption.
- `app/middleware/rate_limiter.py`: FastAPI middleware for key extraction and enforcement.
- `app/metrics/prometheus.py`: Prometheus metric definitions.
- `config/rate_limits.yaml`: Rate limiting rules.
- `tests/unit/test_token_bucket.py`: Unit tests for bucket logic.
- `tests/unit/test_middleware.py`: Unit tests for middleware.
- `tests/integration/test_rate_limiter.py`: End-to-end distributed tests.
- `benchmark/main.go`: Go-based concurrent load generator.
- `docker-compose.yml`: Cluster orchestration.

---

## Implementation Tasks

### Task 1: Configuration System
**Files:**
- Create: `config/rate_limits.yaml`
- Create: `app/config/loader.py`

**Interfaces:**
- Produces: `ConfigLoader.get_rule(path: str, method: str) -> Rule`

- [ ] **Step 1: Create the initial YAML config**
```yaml
rate_limits:
  - path: "/payments"
    method: "POST"
    capacity: 20
    refill_rate: 0.33
  - path: "/health"
    method: "GET"
    capacity: 500
    refill_rate: 8.33
  - path: "*"
    capacity: 100
    refill_rate: 1.67
```

- [ ] **Step 2: Implement the ConfigLoader singleton**
```python
# app/config/loader.py
import yaml
from typing import Optional, List, Dict
from dataclasses import dataclass

@dataclass
class Rule:
    path: str
    method: Optional[str]
    capacity: float
    refill_rate: float

class ConfigLoader:
    _instance = None
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance.rules = []
            cls._instance.load_config()
        return cls._instance

    def load_config(self):
        with open("config/rate_limits.yaml", "r") as f:
            data = yaml.safe_load(f)
            self.rules = [Rule(**r) for r in data["rate_limits"]]

    def get_rule(self, path: str, method: str) -> Rule:
        # Match exact path and method first
        for rule in self.rules:
            if rule.path == path and rule.method == method:
                return rule
        # Match wildcard
        for rule in self.rules:
            if rule.path == "*":
                return rule
        raise ValueError("No rate limit rule found")
```

- [ ] **Step 3: Verify config loading with a simple script**
Run: `python -c "from app.config.loader import ConfigLoader; print(ConfigLoader().get_rule('/payments', 'POST'))"`
Expected: `Rule(path='/payments', method='POST', capacity=20, refill_rate=0.33)`

- [ ] **Step 4: Commit**
`git add config/rate_limits.yaml app/config/loader.py`
`git commit -m "feat: add config loader and rate limit rules"`

---

### Task 2: Redis Lua Token Bucket
**Files:**
- Create: `app/limiter/lua_scripts/token_bucket.lua`
- Create: `app/limiter/token_bucket.py`
- Test: `tests/unit/test_token_bucket.py`

**Interfaces:**
- Produces: `TokenBucket.consume(api_key: str, path: str, capacity: float, refill_rate: float) -> Tuple[bool, float]`

- [ ] **Step 1: Write the Lua script**
```lua
-- app/limiter/lua_scripts/token_bucket.lua
local key = KEYS[1]
local capacity = tonumber(ARGV[1])
local refill_rate = tonumber(ARGV[2])
local now = tonumber(ARGV[3])

local bucket = redis.call('HMGET', key, 'tokens', 'last_refill')
local tokens = tonumber(bucket[1]) or capacity
local last_refill = tonumber(bucket[2]) or now

local elapsed = math.max(0, now - last_refill)
tokens = math.min(capacity, tokens + (elapsed * refill_rate))

local allowed = 0
if tokens >= 1 then
    tokens = tokens - 1
    allowed = 1
end

redis.call('HMSET', key, 'tokens', tokens, 'last_refill', now)
redis.call('EXPIRE', key, math.ceil(capacity / refill_rate * 2))

return {allowed, tokens}
```

- [ ] **Step 2: Implement the Python wrapper**
```python
# app/limiter/token_bucket.py
import time
import redis
from typing import Tuple

class TokenBucket:
    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client
        with open("app/limiter/lua_scripts/token_bucket.lua", "r") as f:
            self.script = self.redis.register_script(f.read())

    def consume(self, api_key: str, path: str, capacity: float, refill_rate: float) -> Tuple[bool, float]:
        key = f"ratelimit:{api_key}:{path}"
        now = time.time()
        # Lua script returns {allowed, tokens}
        res = self.script(keys=[key], args=[capacity, refill_rate, now])
        return bool(res[0]), float(res[1])
```

- [ ] **Step 3: Write failing unit test with fakeredis**
```python
# tests/unit/test_token_bucket.py
import pytest
from fakeredis import FakeRedis
from app.limiter.token_bucket import TokenBucket

def test_token_bucket_allows_initial_request():
    r = FakeRedis()
    bucket = TokenBucket(r)
    allowed, remaining = bucket.consume("user1", "/test", 10, 1)
    assert allowed is True
    assert remaining == 9.0
```

- [ ] **Step 4: Run test and verify it passes**
Run: `pytest tests/unit/test_token_bucket.py`

- [ ] **Step 5: Commit**
`git add app/limiter/lua_scripts/token_bucket.lua app/limiter/token_bucket.py tests/unit/test_token_bucket.py`
`git commit -m "feat: implement atomic token bucket via Lua"`

---

### Task 3: Observability Layer
**Files:**
- Create: `app/metrics/prometheus.py`

**Interfaces:**
- Produces: Global Prometheus metrics objects.

- [ ] **Step 1: Define the Prometheus metrics**
```python
# app/metrics/prometheus.py
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
```

- [ ] **Step 2: Commit**
`git add app/metrics/prometheus.py`
`git commit -m "feat: add prometheus metrics"`

---

### Task 4: Rate Limiting Middleware
**Files:**
- Create: `app/middleware/rate_limiter.py`
- Modify: `app/main.py`
- Test: `tests/unit/test_middleware.py`

**Interfaces:**
- Consumes: `ConfigLoader.get_rule`, `TokenBucket.consume`, `prometheus.metrics`

- [ ] **Step 1: Implement the Middleware logic**
```python
# app/middleware/rate_limiter.py
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
            allowed, remaining = self.token_bucket.consume(
                api_key, path, rule.capacity, rule.refill_rate
            )
            prometheus.REDIS_LATENCY.observe(time.time() - start_time)

            if not allowed:
                prometheus.REQUESTS_TOTAL.labels(status="429").inc()
                prometheus.BLOCKED_TOTAL.inc()
                retry_after = int((1 - remaining) / rule.refill_rate)
                return Response(
                    status_code=429, 
                    headers={
                        "X-RateLimit-Limit": str(rule.capacity),
                        "X-RateLimit-Remaining": str(int(remaining)),
                        "Retry-After": str(retry_after)
                    },
                    content="Too Many Requests"
                )

            prometheus.REQUESTS_TOTAL.labels(status="200").inc()
            prometheus.ALLOWED_TOTAL.inc()
            
            response = await call_next(request)
            response.headers["X-RateLimit-Limit"] = str(rule.capacity)
            response.headers["X-RateLimit-Remaining"] = str(int(remaining))
            return response

        except Exception as e:
            prometheus.REDIS_ERRORS_TOTAL.inc()
            # FAIL-OPEN
            return await call_next(request)
```

- [ ] **Step 2: Set up `app/main.py` and attach middleware**
```python
# app/main.py
from fastapi import FastAPI
import redis
from prometheus_client import make_asgi_app
from app.middleware.rate_limiter import RateLimitMiddleware
from app.limiter.token_bucket import TokenBucket

app = FastAPI()

# Redis Setup
r = redis.Redis(host='redis', port=6379, decode_responses=True)
bucket = TokenBucket(r)

app.add_middleware(RateLimitMiddleware, token_bucket=bucket)

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.post("/payments")
async def payments():
    return {"id": "pay_123"}

# Prometheus metrics endpoint
metrics_app = make_asgi_app()
app.mount("/metrics", metrics_app)
```

- [ ] **Step 3: Write and run middleware unit tests**
(Verify 401, 429, and 200 flows)

- [ ] **Step 4: Commit**
`git add app/middleware/rate_limiter.py app/main.py tests/unit/test_middleware.py`
`git commit -m "feat: add rate limit middleware and fastapi app"`

---

### Task 5: Distributed Cluster (Docker)
**Files:**
- Create: `docker-compose.yml`
- Create: `monitoring/prometheus.yml`

- [ ] **Step 1: Create the Docker Compose cluster**
(Include 3 api-server instances, redis, prometheus, and grafana)

- [ ] **Step 2: Configure Prometheus scraping for all 3 API instances**

- [ ] **Step 3: Commit**
`git add docker-compose.yml monitoring/prometheus.yml`
`git commit -m "feat: docker compose cluster for distributed testing"`

---

### Task 6: Go Benchmark Client
**Files:**
- Create: `benchmark/main.go`
- Create: `benchmark/go.mod`

- [ ] **Step 1: Implement concurrent load generator in Go**
(Using goroutines to fire requests and track p50/p95/p99 latency)

- [ ] **Step 2: Verify benchmark results against the Token Bucket limits**

- [ ] **Step 3: Commit**
`git add benchmark/main.go benchmark/go.mod`
`git commit -m "feat: add go benchmark client"`

---

### Task 7: Integration & Failure Testing
**Files:**
- Create: `tests/integration/test_rate_limiter.py`

- [ ] **Step 1: Write the "Global Limit" test**
(Fire 25 requests across 3 different API instances for a limit-20 endpoint; verify exactly 20 pass)

- [ ] **Step 2: Write the "Redis Outage" fail-open test**
(Stop Redis container, verify requests still pass)

- [ ] **Step 3: Run and verify all integration tests**

- [ ] **Step 4: Final Commit**
`git add tests/integration/test_rate_limiter.py`
`git commit -m "test: verify distributed consistency and fail-open behavior"`
