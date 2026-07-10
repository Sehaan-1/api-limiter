# Design Spec: Distributed API Rate Limiter
Date: 2026-07-10
Status: Approved

## 1. Overview
A production-grade, distributed rate limiting system using a Token Bucket algorithm. The system is designed for high availability, atomicity, and operational observability.

### Goals
- Enforce per-client request budgets across multiple API instances.
- Ensure zero "double-spending" of tokens under concurrent load.
- Maintain high availability via a "fail-open" strategy during Redis outages.
- Provide real-time observability through Prometheus and Grafana.

---

## 2. Architecture

### 2.1 Component Diagram
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
│  │  (goroutines → concurrent load)     │                              │
│  └────────────────────────────────────┘                              │
└─────────────────────────────────────────────────────────────────────┘
```

### 2.2 Data Flow (The Request Lifecycle)
1. **Intercept**: Request is caught by the FastAPI Rate Limiter Middleware.
2. **Identify**: Extract `X-API-Key`. If missing $\rightarrow$ `401 Unauthorized`.
3. **Rule Matching**: 
   - Check `config/rate_limits.yaml`.
   - Match order: Exact Path $\rightarrow$ Wildcard (`*`).
4. **Enforce**: Execute an atomic Lua script in Redis using the key `ratelimit:{api_key}:{endpoint_path}`.
5. **Decision**:
   - **Allow**: Attach `X-RateLimit` headers $\rightarrow$ Proceed to route handler.
   - **Block**: Attach `X-RateLimit` and `Retry-After` headers $\rightarrow$ `429 Too Many Requests`.
6. **Fail-Open**: If Redis is unreachable, log a warning, increment error metric, and allow the request.

---

## 3. Core Implementation Details

### 3.1 Token Bucket Algorithm
We implement the Token Bucket algorithm to allow for bursts while maintaining a steady average rate.

**State Schema (Redis Hash):**
- `tokens`: Current count of available tokens (Float).
- `last_refill`: Unix timestamp of the last refill (Float).
- **TTL**: $2 \times \text{window\_seconds}$ for auto-expiration of idle clients.

### 3.2 Atomic Lua Script
To ensure correctness in a distributed environment, all read-modify-write operations are encapsulated in a single Lua script.

**Lua Logic:**
1. Retrieve `tokens` and `last_refill` from the hash.
2. Compute elapsed time: `now - last_refill`.
3. Refill: `tokens = min(capacity, tokens + (elapsed * refill_rate))`.
4. Consumption: If `tokens >= 1`, decrement `tokens` and set `allowed = 1`.
5. Persist: Update hash and set TTL.
6. Return: `{allowed, current_tokens}`.

### 3.3 Configuration Management
- **File**: `config/rate_limits.yaml`.
- **Loading**: Singleton `ConfigLoader` with hot-reload capabilities via file-system watching.
- **Validation**: Invalid YAML updates are logged as errors and ignored, preserving the last valid state.

### 3.4 Response Headers
| Header | Value |
| :--- | :--- |
| `X-RateLimit-Limit` | Total capacity for the rule. |
| `X-RateLimit-Remaining` | Tokens left after the request. |
| `X-RateLimit-Reset` | Timestamp when bucket is fully refilled. |
| `Retry-After` | (429 only) Seconds until 1 token is available. |

---

## 4. Operational Maturity

### 4.1 Observability
**Prometheus Metrics:**
- `rate_limiter_requests_total{status="..."}`: Total request count.
- `rate_limiter_allowed_total`: Requests passing through.
- `rate_limiter_blocked_total`: Requests blocked.
- `rate_limiter_redis_errors_total`: Count of fail-open events.
- `rate_limiter_redis_latency_seconds`: Histogram of Redis Lua call durations.

**Grafana Dashboard:**
- Real-time rate of Allowed vs Blocked requests.
- 429 error spikes per API key.
- Redis P99 latency tracking.

### 4.2 Verification & Testing
- **Unit Tests**: 
    - Lua script correctness (refill, burst, depletion).
    - YAML loading and wildcard rule matching.
    - Middleware `401` and `429` response logic.
- **Integration Tests**:
    - **Global Limit**: Verify 3 API instances correctly share one Redis bucket.
    - **Resilience**: Kill Redis and verify fail-open behavior.
- **Benchmarking**:
    - Use Go Benchmark Client to saturate the system and verify the exact blocking point of the bucket.

---

## 5. Tech Stack Summary
- **Language/Framework**: Python 3.11 / FastAPI (Async).
- **Storage**: Redis Cluster (3 Shards, HA).
- **Config**: YAML.
- **Observability**: Prometheus & Grafana.
- **Infra**: Docker Compose.
- **Benchmarking**: Go (Goroutines).
