import time
from unittest.mock import patch
from fakeredis import FakeRedis
from app.limiter.token_bucket import TokenBucket

def test_token_bucket_allows_initial_request():
    r = FakeRedis()
    bucket = TokenBucket(r)
    capacity = 10.0
    refill_rate = 1.0

    allowed, remaining = bucket.consume("user1", "/test", capacity, refill_rate)
    assert allowed is True
    assert remaining == capacity - 1

def test_token_bucket_respects_burst_capacity():
    r = FakeRedis()
    bucket = TokenBucket(r)
    capacity = 5.0
    refill_rate = 1.0

    for _ in range(int(capacity)):
        allowed, _ = bucket.consume("user1", "/test", capacity, refill_rate)
        assert allowed is True

    allowed, remaining = bucket.consume("user1", "/test", capacity, refill_rate)
    assert allowed is False
    assert remaining == 0.0

def test_token_bucket_refills_over_time():
    r = FakeRedis()
    bucket = TokenBucket(r)
    capacity = 5.0
    refill_rate = 1.0

    # Use a fixed time to avoid issues with real time
    start_time = 1000.0
    with patch('app.limiter.token_bucket.time.time') as mock_time:
        mock_time.return_value = start_time

        # Empty the bucket
        for _ in range(int(capacity)):
            bucket.consume("user1", "/test", capacity, refill_rate)

        allowed, _ = bucket.consume("user1", "/test", capacity, refill_rate)
        assert allowed is False

        # Move forward by 2 seconds
        mock_time.return_value = start_time + 2

        # Should have refilled 2 tokens (2 * refill_rate = 2)
        # It consumes 1, so remaining should be 1.0
        allowed, remaining = bucket.consume("user1", "/test", capacity, refill_rate)
        assert allowed is True
        assert remaining == 1.0

def test_token_bucket_sets_ttl():
    r = FakeRedis()
    bucket = TokenBucket(r)
    capacity = 10.0
    refill_rate = 1.0

    bucket.consume("user1", "/test", capacity, refill_rate)

    key = "ratelimit:user1:/test"
    ttl = r.ttl(key)
    # Lua: math.ceil(10 / 1 * 2) = 20
    assert ttl == 20
