# Distributed Rate Limiter

A production-grade, distributed API rate limiter built with FastAPI and Redis Cluster. It implements the Token Bucket algorithm via atomic Lua scripts to guarantee per-key, per-endpoint rate enforcement across multiple API server replicas without race conditions. Designed for interview-style demonstration of distributed systems, state management, and observability engineering.

## Key Features

- Token Bucket algorithm executed atomically in Redis via Lua scripts
- Per-API-key, per-endpoint rate limiting with path and method matching
- Hot-reloadable YAML configuration — no restart required
- Fail-open behavior: Redis failures allow traffic through instead of hard-failing
- Full RFC-compliant response headers: `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset`, `Retry-After`
- Prometheus metrics with Grafana dashboards
- Multi-replica API servers sharing a single Redis Cluster state
- Concurrent Go load generator for benchmarking with p50/p95/p99 latency reporting

---

## Tech Stack

| Layer | Technology |
|---|---|
| Language (API) | Python 3.11 |
| Framework | FastAPI + Uvicorn |
| Rate Limiter State | Redis Cluster (6-node via Bitnami) |
| Lua Scripts | Atomic token bucket logic, registered via `redis.register_script` |
| Configuration | YAML with file-watcher for hot reload |
| Metrics | `prometheus-client`, scraped by Prometheus |
| Visualization | Grafana |
| Load Generator | Go (stdlib only) |
| Containerization | Docker + Docker Compose |

---

## Prerequisites

- Docker and Docker Compose
- Go 1.21+ (for the benchmark tool only)
- Python 3.11+ (for running the API outside Docker)
- `pip` (for local development without Docker)

---

## Getting Started

### 1. Clone the Repository

```bash
git clone https://github.com/your-org/distributed-rate-limiter.git
cd distributed-rate-limiter
```

### 2. Start the Full Stack with Docker Compose

This command starts 6 Redis Cluster nodes, 3 API server replicas, Prometheus, and Grafana.

```bash
docker compose up --build
```

Services will be available at:

| Service | URL |
|---|---|
| API Server 1 | http://localhost:8001 |
| API Server 2 | http://localhost:8002 |
| API Server 3 | http://localhost:8003 |
| Prometheus | http://localhost:9090 |
| Grafana | http://localhost:3000 |

### 3. Verify the Stack

Check the health endpoint on any replica:

```bash
curl http://localhost:8001/health
# {"status": "ok"}
```

Send an authenticated request:

```bash
curl -H "X-API-Key: my-key" http://localhost:8001/payments
```

Trigger the rate limit by sending many requests quickly:

```bash
for i in $(seq 1 20); do
  curl -si -H "X-API-Key: my-key" http://localhost:8001/payments | head -1
done
```

You should see `HTTP/1.1 200 OK` responses followed by `HTTP/1.1 429 Too Many Requests`.

---

## Local Development (Without Docker)

### 1. Install Python Dependencies

```bash
pip install -r requirements.txt
```

### 2. Start a Local Redis Instance

```bash
docker run --rm -p 6379:6379 redis:7-alpine
```

### 3. Update the Redis Host

In `app/main.py`, change the Redis host from `redis` to `localhost` for local runs:

```python
r = redis.Redis(host='localhost', port=6379, decode_responses=True)
```

### 4. Create the Rate Limit Config

Ensure `config/rate_limits.yaml` exists. See the Configuration section below.

### 5. Run the API Server

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

---

## Configuration

Rate limit rules are defined in `config/rate_limits.yaml`. The path is controlled by the `RATE_LIMIT_CONFIG_PATH` environment variable, defaulting to `config/rate_limits.yaml`.

```yaml
rate_limits:
  - path: /payments
    method: POST
    capacity: 10.0
    refill_rate: 1.0

  - path: /health
    method: GET
    capacity: 1000.0
    refill_rate: 100.0

  - path: "*"
    capacity: 5.0
    refill_rate: 0.5
```

| Field | Type | Description |
|---|---|---|
| `path` | string | Endpoint path. Use `"*"` as a wildcard catch-all. |
| `method` | string | HTTP method (`GET`, `POST`, etc.). Optional for wildcards. |
| `capacity` | float | Maximum tokens the bucket can hold (burst limit). |
| `refill_rate` | float | Tokens added per second (sustained rate). |

The configuration file is watched every second. Any change is automatically picked up with no server restart.

**Rule matching order:**
1. Exact path + method match
2. Wildcard `*` path match
3. If no match, the request passes through without rate limiting

---

## Architecture

### Directory Structure

```
.
├── app/
│   ├── main.py                    # FastAPI app, Redis setup, route definitions
│   ├── middleware/
│   │   └── rate_limiter.py        # Starlette middleware: auth, rule lookup, token consume
│   ├── limiter/
│   │   ├── token_bucket.py        # TokenBucket class wrapping the Lua script
│   │   └── lua_scripts/
│   │       └── token_bucket.lua   # Atomic Redis Lua script (read-modify-write)
│   ├── metrics/
│   │   └── prometheus.py          # Counter and Histogram definitions
│   └── config/
│       └── loader.py              # Singleton config loader with file-watcher thread
├── benchmark/
│   ├── main.go                    # Concurrent Go load generator
│   └── go.mod
├── tests/
│   ├── unit/
│   │   ├── test_token_bucket.py   # Unit tests for the TokenBucket class
│   │   └── test_middleware.py     # Unit tests for RateLimitMiddleware
│   └── integration/
│       └── test_rate_limiter.py   # Integration tests against a live Redis instance
├── monitoring/
│   └── prometheus.yml             # Prometheus scrape config
├── Dockerfile                     # Python 3.11 slim image, runs uvicorn
├── docker-compose.yml             # Full stack: Redis Cluster + API x3 + Prometheus + Grafana
└── requirements.txt
```

### Request Lifecycle

```
Incoming Request
       |
       v
RateLimitMiddleware.dispatch()
       |
       +-- No X-API-Key header? --> 401 Unauthorized
       |
       +-- ConfigLoader.get_rule(path, method)
       |       |
       |       +-- Exact match   --> Rule
       |       +-- Wildcard      --> Rule
       |       +-- No match      --> pass-through (no limiting)
       |
       +-- TokenBucket.consume(api_key, path, capacity, refill_rate)
               |
               +-- Executes Lua script atomically in Redis
               |       key = "ratelimit:{api_key}:{path}"
               |       Reads tokens + last_refill, refills elapsed tokens,
               |       consumes 1 if available, writes new state atomically
               |
               +-- allowed=True  --> Sets X-RateLimit-* headers, forwards request
               +-- allowed=False --> 429 with Retry-After and X-RateLimit-* headers
               +-- Redis error   --> Fail-open: forwards request, increments error counter
```

### Token Bucket Algorithm

The Token Bucket is implemented inside a Redis Lua script. Lua scripts in Redis execute atomically — no other command runs between the read and write, eliminating race conditions across replicas.

```
State per key: { tokens: float, last_refill: unix_timestamp }

On each consume call:
  elapsed    = now - last_refill
  new_tokens = min(capacity, tokens + elapsed * refill_rate)

  if new_tokens >= 1:
    tokens  = new_tokens - 1
    allowed = true
  else:
    tokens  = new_tokens
    allowed = false

  Write {tokens, now} back to Redis
  Return (allowed, remaining_tokens)
```

### Redis Key Design

```
ratelimit:{api_key}:{path}
```

Examples:
- `ratelimit:key-abc:/payments`
- `ratelimit:key-xyz:/health`

Each key is a Redis Hash holding `tokens` and `last_refill`. Keys are scoped per API key per endpoint, so different clients have fully independent buckets.

### Configuration Hot Reload

`ConfigLoader` is a singleton that runs a daemon background thread. The thread polls `os.path.getmtime()` every second and calls `load_config()` if the file was modified. The rule list is replaced atomically as a Python list assignment, which is thread-safe under the GIL.

### Fail-Open Behavior

If Redis is unreachable or the Lua script throws, `RateLimitMiddleware` catches the exception, increments `rate_limiter_redis_errors_total`, and calls `call_next(request)` to pass the request through. This prioritizes availability over strict enforcement during infrastructure failures.

### Prometheus Metrics

| Metric | Type | Description |
|---|---|---|
| `rate_limiter_requests_total{status}` | Counter | Total requests labeled by outcome (`allowed`, `429`, `401`) |
| `rate_limiter_allowed_total` | Counter | Requests that passed the rate limit check |
| `rate_limiter_blocked_total` | Counter | Requests rejected with 429 |
| `rate_limiter_redis_errors_total` | Counter | Redis connectivity or script failures |
| `rate_limiter_redis_latency_seconds` | Histogram | Latency of the Redis Lua script call |

Metrics are exposed at `/metrics` on each API server and scraped by Prometheus every 15 seconds.

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `REDIS_HOST` | `redis` | Redis host. Set to `localhost` for local dev. |
| `RATE_LIMIT_CONFIG_PATH` | `config/rate_limits.yaml` | Path to the rate limit rules YAML file. |

---

## Testing

### Unit Tests

Unit tests use `pytest` with `unittest.mock` to isolate Redis and filesystem dependencies.

```bash
pytest tests/unit/ -v
```

Key test files:
- `tests/unit/test_token_bucket.py` — Tests `TokenBucket.consume()` for allowed, blocked, and edge-case behavior with a mocked Redis script.
- `tests/unit/test_middleware.py` — Tests `RateLimitMiddleware` for auth rejection, rule matching, header injection, fail-open, and 429 responses.

### Integration Tests

Integration tests require a running Redis instance. Start one before running:

```bash
docker run --rm -p 6379:6379 redis:7-alpine
```

```bash
pytest tests/integration/ -v
```

Key test file:
- `tests/integration/test_rate_limiter.py` — Sends real requests against the FastAPI app with a live Redis, verifying that the rate limit is enforced end-to-end at the configured capacity.

### Run All Tests

```bash
pytest tests/ -v
```

---

## Benchmarking

The `benchmark/` directory contains a concurrent Go load generator. It fires HTTP requests from N goroutines for a fixed duration, then prints p50/p95/p99 latencies and request outcome counts. Memory usage is bounded via reservoir sampling — at most 10,000 latency samples are retained regardless of request count.

### Build

```bash
cd benchmark
go build -o bench .
```

### Run

```bash
./bench \
  --target-url http://localhost:8001/payments \
  --api-key my-test-key \
  --concurrency 50 \
  --duration 30
```

| Flag | Default | Description |
|---|---|---|
| `--target-url` | `http://localhost:8080/api/limit` | Target endpoint |
| `--api-key` | `test-key` | Value sent in `X-API-Key` header |
| `--concurrency` | `10` | Number of concurrent goroutines |
| `--duration` | `10` | Test duration in seconds |

### Sample Output

```
Starting benchmark: http://localhost:8001/payments
Concurrency: 50, Duration: 30s

--- Benchmark Summary ---
Total Requests: 14823
200 (Allowed):  312
429 (Blocked):  14511
p50 Latency:    3.2ms
p95 Latency:    8.7ms
p99 Latency:    19.4ms
Total Duration:  30.002s
```

---

## Observability

### Prometheus

Prometheus scrapes all three API server replicas every 15 seconds using `monitoring/prometheus.yml`.

Access the Prometheus UI at http://localhost:9090. Useful queries:

```promql
# Request rate by outcome over last 5 minutes
rate(rate_limiter_requests_total[5m])

# Percentage of requests blocked
rate(rate_limiter_blocked_total[5m]) / rate(rate_limiter_requests_total[5m])

# 95th percentile Redis latency
histogram_quantile(0.95, rate(rate_limiter_redis_latency_seconds_bucket[5m]))

# Redis error rate
rate(rate_limiter_redis_errors_total[5m])
```

### Grafana

Grafana is available at http://localhost:3000 (default credentials: `admin` / `admin`).

To connect Grafana to the Prometheus instance:
1. Go to **Connections > Data Sources > Add data source**
2. Select **Prometheus**
3. Set URL to `http://prometheus:9090`
4. Click **Save & Test**

---

## Deployment

The project is fully containerized. For production, replace the Bitnami Redis Cluster with a managed service (AWS ElastiCache, Google Memorystore, Azure Cache for Redis) and deploy the API containers behind a load balancer.

### Build the API Image

```bash
docker build -t rate-limiter:latest .
```

### Run a Single API Container

```bash
docker run -p 8000:8000 \
  -e REDIS_HOST=your-redis-host \
  -e RATE_LIMIT_CONFIG_PATH=/config/rate_limits.yaml \
  -v $(pwd)/config:/config \
  rate-limiter:latest
```

---

## Troubleshooting

**`ConnectionRefusedError` on startup**

Redis is not yet ready when the API container starts. Restart the API containers after the Redis cluster has formed:

```bash
docker compose restart api-server-1 api-server-2 api-server-3
```

**All requests return 401**

The `X-API-Key` header is missing. Include it in every request:

```bash
curl -H "X-API-Key: my-key" http://localhost:8001/payments
```

**Rate limit config changes are not picked up**

Verify `RATE_LIMIT_CONFIG_PATH` points to the correct file. The watcher polls every 1 second. Check container logs for `Error loading config:` messages indicating a YAML parse error.

**Go benchmark build fails**

Ensure Go 1.21+ is installed. The benchmark uses only the Go standard library.

```bash
go version
```

**`/metrics` returns no data**

Prometheus metrics are registered on first request. Send at least one request through the API before checking `/metrics`.
