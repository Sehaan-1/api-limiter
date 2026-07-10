# PRD: Distributed API Rate Limiter

> A production-grade, Stripe-style rate limiting system built to demonstrate
> distributed state management, infrastructure protection, and operational maturity.

---

## 1. Overview

### Problem Statement

In high-scale API environments (like Stripe's), a single misbehaving client — one
that spikes traffic unexpectedly — can saturate backend resources and degrade the
experience for all other users. A robust rate limiter must:

- Enforce per-client request budgets
- Share state across multiple application servers (distributed)
- Survive infrastructure failures gracefully
- Be observable in production

### Goal

Build a production-quality, distributed API rate limiter as a showcase project that
demonstrates deep understanding of the concepts Stripe evaluates in system design and
infrastructure interviews.

---

## 2. System Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                         Docker Compose Cluster                       │
│                                                                       │
│  ┌────────────┐    ┌────────────┐    ┌────────────┐                  │
│  │  FastAPI   │    │  FastAPI   │    │  FastAPI   │  ← API Servers   │
│  │ Instance 1 │    │ Instance 2 │    │ Instance 3 │                  │
│  └────────┬───┘    └─────┬──────┘    └─────┬──────┘                  │
│           └──────────────┼─────────────────┘                         │
│                          │ Rate limit state (Lua scripts)            │
│              ┌───────────▼──────────────────────┐                   │
│              │         Redis Cluster             │                   │
│              │  (Primary + 2 Replicas, 3 Shards) │                   │
│              └───────────┬──────────────────────┘                   │
│                          │                                           │
│  ┌────────────┐    ┌──────▼───────┐    ┌────────────┐               │
│  │ Prometheus │◄───│   Metrics    │───►│  Grafana   │               │
│  └────────────┘    │   Exporter   │    │ Dashboard  │               │
│                    └──────────────┘    └────────────┘               │
│                                                                       │
│  ┌────────────────────────────────────┐                              │
│  │     Go Benchmark Client            │                              │
│  │  (goroutines → concurrent load)    │                              │
│  └────────────────────────────────────┘                              │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 3. Algorithm: Token Bucket

### Why Token Bucket

- **Burst-friendly**: Clients can absorb short traffic spikes using accumulated tokens
- **Stripe-aligned**: Mirrors how Stripe's own rate limiting conceptually works
- **Intuitive to explain in interviews**: Clear mental model, easy to whiteboard

### How It Works

Each API key gets a "bucket" in Redis with two fields:
- `tokens` — current count of available request credits
- `last_refill` — Unix timestamp of the last token refill

**On every request:**

```
1. Compute elapsed = now - last_refill
2. Compute new_tokens = elapsed * (capacity / window_seconds)
3. tokens = min(capacity, stored_tokens + new_tokens)
4. If tokens >= 1: consume 1 token, allow request → 200
5. Else: reject → 429 Too Many Requests
```

**Atomicity:** Steps 1–5 execute as a **single Redis Lua script** — no race conditions,
no double-spending under concurrent load.

### Token Bucket Parameters (per endpoint rule)

| Parameter        | Description                              | Example     |
|------------------|------------------------------------------|-------------|
| `capacity`       | Max tokens (burst ceiling)               | `20`        |
| `refill_rate`    | Tokens per second replenished            | `5`         |
| `window_seconds` | Duration used for rate calculation       | `60`        |

---

## 4. Tech Stack

| Layer              | Technology                              |
|--------------------|-----------------------------------------|
| API Server         | **Python / FastAPI** (async)            |
| Rate Limit Store   | **Redis Cluster** (3 shards, HA)        |
| Middleware         | FastAPI middleware (request interception)|
| Benchmarking       | **Go** (goroutine-based concurrent load)|
| Observability      | **Prometheus + Grafana**                |
| Container Infra    | **Docker Compose**                      |
| Testing            | **pytest + fakeredis** (unit) + **real Redis** (integration) |
| Config             | **YAML file** (hot-watched for reload)  |

---

## 5. Functional Requirements

### FR-1: Token Bucket Rate Limiting Middleware

- Intercept **every incoming HTTP request** via FastAPI middleware
- Extract the `X-API-Key` header to identify the client
- If the header is missing → return `401 Unauthorized`
- Perform an atomic Redis Lua token bucket check
- On success → pass request to handler
- On failure → return `429 Too Many Requests`

### FR-2: Per-Endpoint Rate Limit Rules

Rules are defined per HTTP route pattern. Example config:

```yaml
rate_limits:
  - path: "/payments"
    method: "POST"
    capacity: 20
    refill_rate: 0.33   # ~20 req/min
  - path: "/health"
    method: "GET"
    capacity: 500
    refill_rate: 8.33   # ~500 req/min
  - path: "*"           # default / catch-all
    capacity: 100
    refill_rate: 1.67   # ~100 req/min
```

Rule matching order: **exact path → wildcard**

### FR-3: Rate Limit Response Headers

All responses (allowed AND rejected) must include:

| Header                    | Value                                      |
|---------------------------|--------------------------------------------|
| `X-RateLimit-Limit`       | Max tokens (capacity for this endpoint)    |
| `X-RateLimit-Remaining`   | Tokens left after this request             |
| `X-RateLimit-Reset`       | Unix timestamp when bucket is full again   |
| `Retry-After`             | Seconds until next token available (429 only) |

### FR-4: Redis Cluster State

- Use Redis Cluster with **3 primary shards + 3 replicas**
- Token bucket state keyed as: `ratelimit:{api_key}:{endpoint_path}`
- TTL set to `2 × window_seconds` to auto-expire idle buckets

### FR-5: YAML Configuration

- Config file at `config/rate_limits.yaml`
- Loaded at startup
- File-watched: changes apply without restart (via watchdog or polling)
- If config is invalid → log error, keep previous valid config

### FR-6: Failure Mode — Redis Unavailable

When Redis is unreachable:
1. **Fail-open**: Allow the request through (availability > safety)
2. **Emit a Prometheus metric**: `rate_limiter_redis_errors_total` (increment)
3. **Log a structured warning**: `{"event": "redis_unavailable", "action": "fail_open"}`
4. Alert should be wired to Grafana → on-call notification

### FR-7: Go Benchmark Client

A standalone Go binary (`/benchmark/main.go`) that:

- Accepts CLI flags: `--concurrency`, `--duration`, `--target-url`, `--api-key`
- Spawns N goroutines, each firing requests for the specified duration
- Tracks per-request: latency, HTTP status code
- Outputs a final report:
  - Total requests sent
  - `200` count (allowed) vs `429` count (blocked)
  - p50 / p95 / p99 latency
  - Requests/sec throughput

### FR-8: Observability

**Prometheus metrics exposed at `/metrics`:**

| Metric                              | Type      | Description                        |
|-------------------------------------|-----------|------------------------------------|
| `rate_limiter_requests_total`       | Counter   | All requests, labeled by status    |
| `rate_limiter_allowed_total`        | Counter   | Requests passed through            |
| `rate_limiter_blocked_total`        | Counter   | Requests rejected (429)            |
| `rate_limiter_redis_latency_seconds`| Histogram | Time spent in Redis Lua call       |
| `rate_limiter_redis_errors_total`   | Counter   | Redis connectivity failures        |

**Grafana Dashboard panels:**
- Request rate (allowed vs blocked) over time
- 429 rate % per API key
- Redis latency percentiles
- Redis error spike alert

---

## 6. Non-Functional Requirements

| Requirement       | Target                                            |
|-------------------|---------------------------------------------------|
| **Latency**       | < 2ms overhead per request (Redis round-trip)     |
| **Correctness**   | Zero double-spend under concurrent load (Lua atomicity) |
| **Availability**  | Fail-open on Redis outage, never block healthy traffic silently |
| **Scalability**   | Horizontally scale FastAPI instances; Redis Cluster handles sharding |
| **Config reload** | Rate rule changes apply within 5 seconds, no restart |

---

## 7. API Endpoints

The service exposes demo endpoints to exercise the rate limiter:

```
GET  /health         → 200 {"status": "ok"}  (high limit: 500 req/min)
POST /payments       → 200 {"id": "pay_xxx"} (low limit: 20 req/min)
GET  /customers      → 200 [...]             (medium limit: 100 req/min)
GET  /metrics        → Prometheus text format (no rate limiting)
```

All endpoints (except `/metrics`) require `X-API-Key` header.

---

## 8. Testing Plan

### Unit Tests (`pytest` + `fakeredis`)

- [ ] Token bucket allows requests up to capacity
- [ ] Token bucket blocks at capacity (`429`)
- [ ] Tokens refill correctly over simulated time
- [ ] Concurrent requests don't double-spend (simulated with threading)
- [ ] Missing `X-API-Key` returns `401`
- [ ] Rate limit headers are correct on both allowed and blocked responses
- [ ] Redis error triggers fail-open behavior
- [ ] YAML config loads correctly and validates rules

### Integration Tests (real Redis via Docker)

- [ ] End-to-end: fire 25 requests at a limit-20 endpoint; verify 20 pass, 5 block
- [ ] Multi-instance: two FastAPI containers share state (shared Redis); same API key throttled globally
- [ ] Burst then refill: exhaust bucket, wait for partial refill, verify proportional allow
- [ ] Redis cluster failover: kill a Redis primary, verify fail-open behavior

---

## 9. Project Structure

```
distributed-rate-limiter/
├── docker-compose.yml           # Full cluster: FastAPI × 3, Redis Cluster, Prometheus, Grafana
├── config/
│   └── rate_limits.yaml         # Endpoint rate limit rules
├── app/
│   ├── main.py                  # FastAPI app, routes
│   ├── middleware/
│   │   └── rate_limiter.py      # HTTP middleware: extracts key, calls Redis
│   ├── limiter/
│   │   ├── token_bucket.py      # Token bucket logic + Lua script
│   │   └── lua_scripts/
│   │       └── token_bucket.lua # Atomic Redis Lua script
│   ├── config/
│   │   └── loader.py            # YAML loader + file watcher
│   └── metrics/
│       └── prometheus.py        # Prometheus counters/histograms
├── tests/
│   ├── unit/
│   │   ├── test_token_bucket.py
│   │   └── test_middleware.py
│   └── integration/
│       └── test_rate_limiter.py
├── benchmark/
│   ├── main.go                  # Go concurrent load generator
│   └── go.mod
├── monitoring/
│   ├── prometheus.yml           # Scrape config
│   └── grafana/
│       └── dashboards/
│           └── rate_limiter.json
└── README.md
```

---

## 10. Milestones

| # | Milestone                                | Deliverable                                          |
|---|------------------------------------------|------------------------------------------------------|
| 1 | **Core Algorithm**                       | Token bucket Lua script, Redis schema, unit tests    |
| 2 | **FastAPI Middleware**                   | Middleware, headers, 429 responses, 401 for no key   |
| 3 | **Configuration System**                | YAML loader, per-endpoint rules, hot-reload          |
| 4 | **Docker Compose Cluster**              | 3 FastAPI instances + Redis Cluster + load balancer  |
| 5 | **Observability**                        | Prometheus metrics + Grafana dashboard               |
| 6 | **Go Benchmark Client**                 | Goroutine load generator, report output              |
| 7 | **Integration Tests + Failure Testing** | Redis failover test, multi-instance shared state     |
| 8 | **Documentation**                        | README with architecture diagram, setup, demo GIF    |

---

## 11. Why This Impresses Stripe

| Stripe Value              | How This Project Demonstrates It                      |
|---------------------------|-------------------------------------------------------|
| **Distributed systems**   | Redis Cluster + multiple API instances sharing state  |
| **Correctness at scale**  | Lua atomicity eliminates race conditions              |
| **Infrastructure thinking** | Fail-open pattern; Redis HA; Prometheus alerting   |
| **Operational maturity**  | Grafana dashboards, structured logging, metrics       |
| **API design**            | Standard rate limit headers (RFC 6585 compliant)      |
| **Testing rigor**         | Unit + integration tests covering edge cases          |
| **Performance awareness** | Go benchmark client measuring p99 latency             |
