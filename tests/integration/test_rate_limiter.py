import asyncio
import httpx
import pytest
import time
import subprocess
import re

# Target endpoints for the distributed API instances
# In a real Docker Compose setup, these would be the service names or mapped ports
API_INSTANCES = [
    "http://api-1:8000",
    "http://api-2:8000",
    "http://api-3:8000",
]
REDIS_CONTAINER_NAME = "redis"

async def send_request(client, url, api_key):
    headers = {"X-API-Key": api_key}
    return await client.get(f"{url}/payments", headers=headers)

def get_metric_value(text, metric_name):
    # Prometheus format: metric_name{labels} value
    pattern = rf"{metric_name}.*?\s+([\d\.]+)"
    match = re.search(pattern, text)
    return float(match.group(1)) if match else 0.0

@pytest.mark.asyncio
async def test_global_rate_limit():
    """
    Fire 25 requests across 3 different API instances for a limit-20 endpoint;
    verify exactly 20 pass.
    """
    api_key = "test-global-limit-key"

    async with httpx.AsyncClient() as client:
        tasks = []
        for i in range(25):
            instance = API_INSTANCES[i % 3]
            tasks.append(send_request(client, instance, api_key))

        responses = await asyncio.gather(*tasks)

        allowed = [r for r in responses if r.status_code == 200]
        blocked = [r for r in responses if r.status_code == 429]

        assert len(allowed) == 20, f"Expected 20 allowed requests, got {len(allowed)}"
        assert len(blocked) == 5, f"Expected 5 blocked requests, got {len(blocked)}"

@pytest.mark.asyncio
async def test_fail_open_redis_outage():
    """
    Simulate a Redis outage (stop Redis container), verify requests still pass,
    and verify that rate_limiter_redis_errors_total is incremented.
    """
    api_key = "test-fail-open-key"

    async with httpx.AsyncClient() as client:
        # 1. Verify initial state: Redis is up and limiting
        resp = await send_request(client, API_INSTANCES[0], api_key)
        assert resp.status_code == 200

        # Get initial error count
        metrics_resp = await client.get(f"{API_INSTANCES[0]}/metrics")
        initial_errors = get_metric_value(metrics_resp.text, "rate_limiter_redis_errors_total")

        # 2. Simulate outage: stop redis
        subprocess.run(["docker", "stop", REDIS_CONTAINER_NAME], check=True)

        try:
            # 3. Verify fail-open: requests should still pass
            # We try a few times to ensure the middleware has actually hit the exception
            for _ in range(5):
                resp = await send_request(client, API_INSTANCES[0], api_key)
                assert resp.status_code == 200

            # 4. Verify metric incremented
            metrics_resp = await client.get(f"{API_INSTANCES[0]}/metrics")
            current_errors = get_metric_value(metrics_resp.text, "rate_limiter_redis_errors_total")

            assert current_errors > initial_errors, "Redis error metric should have incremented"

        finally:
            # Restart redis for other tests
            subprocess.run(["docker", "start", REDIS_CONTAINER_NAME], check=True)

@pytest.mark.asyncio
async def test_burst_and_refill():
    """
    Exhaust the bucket, wait for a partial refill, and verify the
    proportional number of requests are allowed.
    """
    api_key = "test-burst-refill-key"
    capacity = 20
    refill_rate = 1 # 1 token per second

    async with httpx.AsyncClient() as client:
        # 1. Exhaust the bucket
        tasks = [send_request(client, API_INSTANCES[0], api_key) for _ in range(capacity)]
        responses = await asyncio.gather(*tasks)
        assert all(r.status_code == 200 for r in responses)

        # Verify next request is blocked
        resp = await send_request(client, API_INSTANCES[0], api_key)
        assert resp.status_code == 429

        # 2. Wait for partial refill (e.g., 5 seconds for 5 tokens)
        wait_time = 5
        await asyncio.sleep(wait_time)

        # 3. Verify proportional requests are allowed
        # Try to send 20 requests again
        tasks = [send_request(client, API_INSTANCES[0], api_key) for _ in range(capacity)]
        responses = await asyncio.gather(*tasks)

        allowed = [r for r in responses if r.status_code == 200]
        # We expect roughly 5 tokens. Margin of error for timing.
        assert 4 <= len(allowed) <= 6, f"Expected ~5 tokens, got {len(allowed)}"
