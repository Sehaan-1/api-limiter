from fastapi import FastAPI
import redis
from prometheus_client import make_asgi_app
from app.middleware.rate_limiter import RateLimitMiddleware
from app.limiter.token_bucket import TokenBucket

app = FastAPI()

# Redis Setup
# ponytail: using 'redis' host as per requirements, but in local dev this might need to be 'localhost'
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
