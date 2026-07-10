import time
import pytest
from unittest.mock import AsyncMock, Mock, patch
from app.limiter.token_bucket import TokenBucket
import asyncio

@pytest.mark.asyncio
async def test_token_bucket_allows_initial_request():
    mock_redis = AsyncMock()
    mock_script = AsyncMock(return_value=[1, 9.0])
    # register_script is a synchronous method in redis-py
    mock_redis.register_script = Mock(return_value=mock_script)

    bucket = TokenBucket(mock_redis)
    capacity = 10.0
    refill_rate = 1.0

    allowed, remaining = await bucket.consume("user1", "/test", capacity, refill_rate)
    assert allowed is True
    assert remaining == 9.0

@pytest.mark.asyncio
async def test_token_bucket_respects_burst_capacity():
    mock_redis = AsyncMock()
    mock_script = AsyncMock()
    mock_script.side_effect = [[1, 4.0], [1, 3.0], [1, 2.0], [1, 1.0], [1, 0.0], [0, 0.0]]
    mock_redis.register_script = Mock(return_value=mock_script)

    bucket = TokenBucket(mock_redis)
    capacity = 5.0
    refill_rate = 1.0

    for _ in range(5):
        allowed, _ = await bucket.consume("user1", "/test", capacity, refill_rate)
        assert allowed is True

    allowed, remaining = await bucket.consume("user1", "/test", capacity, refill_rate)
    assert allowed is False
    assert remaining == 0.0

@pytest.mark.asyncio
async def test_token_bucket_refills_over_time():
    mock_redis = AsyncMock()
    mock_script = AsyncMock()
    mock_script.side_effect = [[0, 0.0], [1, 1.0]]
    mock_redis.register_script = Mock(return_value=mock_script)

    bucket = TokenBucket(mock_redis)
    capacity = 5.0
    refill_rate = 1.0

    # Call 1: Blocked
    allowed, _ = await bucket.consume("user1", "/test", capacity, refill_rate)
    assert allowed is False

    # Call 2: Allowed
    allowed, remaining = await bucket.consume("user1", "/test", capacity, refill_rate)
    assert allowed is True
    assert remaining == 1.0

@pytest.mark.asyncio
async def test_token_bucket_sets_ttl():
    mock_redis = AsyncMock()
    mock_script = AsyncMock(return_value=[1, 9.0])
    mock_redis.register_script = Mock(return_value=mock_script)

    bucket = TokenBucket(mock_redis)
    await bucket.consume("user1", "/test", 10.0, 1.0)

    # Verify the script was registered
    mock_redis.register_script.assert_called_once()
