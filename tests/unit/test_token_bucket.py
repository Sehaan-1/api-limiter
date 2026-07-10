from unittest.mock import MagicMock
from fakeredis import FakeRedis
from app.limiter.token_bucket import TokenBucket

def test_token_bucket_allows_initial_request():
    r = FakeRedis()
    bucket = TokenBucket(r)

    # Mock the Lua script result: {allowed, tokens}
    bucket.script = MagicMock(return_value=[1, 9.0])

    allowed, remaining = bucket.consume("user1", "/test", 10, 1)
    assert allowed is True
    assert remaining == 9.0
    bucket.script.assert_called_once()

def test_token_bucket_consumes_until_empty():
    r = FakeRedis()
    bucket = TokenBucket(r)

    # Simulate a sequence of responses from Lua
    # 5 allowed, then 1 denied
    bucket.script.side_effect = [[1, 4.0], [1, 3.0], [1, 2.0], [1, 1.0], [1, 0.0], [0, 0.0]]
    # Wait, I need to mock the script attribute
    bucket.script = MagicMock()
    bucket.script.side_effect = [[1, 4.0], [1, 3.0], [1, 2.0], [1, 1.0], [1, 0.0], [0, 0.0]]

    capacity = 5
    refill_rate = 1

    for _ in range(5):
        allowed, _ = bucket.consume("user1", "/test", capacity, refill_rate)
        assert allowed is True

    allowed, remaining = bucket.consume("user1", "/test", capacity, refill_rate)
    assert allowed is False
    assert remaining == 0.0

def test_token_bucket_refills():
    r = FakeRedis()
    bucket = TokenBucket(r)

    bucket.script = MagicMock()
    # First call: deny (0 tokens), Second call: allow (after refill)
    bucket.script.side_effect = [[0, 0.0], [1, 0.0]]

    capacity = 5
    refill_rate = 1

    allowed, _ = bucket.consume("user1", "/test", capacity, refill_rate)
    assert allowed is False

    # simulate time passing by calling consume again
    allowed, remaining = bucket.consume("user1", "/test", capacity, refill_rate)
    assert allowed is True

def test_token_bucket_does_not_exceed_capacity():
    r = FakeRedis()
    bucket = TokenBucket(r)

    bucket.script = MagicMock(return_value=[1, 4.0])

    capacity = 5
    refill_rate = 1

    allowed, remaining = bucket.consume("user1", "/test", capacity, refill_rate)
    assert allowed is True
    assert remaining == 4.0
