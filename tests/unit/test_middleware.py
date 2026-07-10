from fastapi import FastAPI, Response
from fastapi.testclient import TestClient
from unittest.mock import MagicMock, patch
import pytest
from app.middleware.rate_limiter import RateLimitMiddleware
from app.config.loader import Rule
from app.limiter.token_bucket import TokenBucket

@pytest.fixture
def app_with_mocks():
    # Create a fresh app for each test
    app = FastAPI()
    app.add_route("/", lambda request: Response(content="Hello World"))

    # Mock TokenBucket
    mock_bucket = MagicMock(spec=TokenBucket)

    # We need to patch ConfigLoader BEFORE adding the middleware
    # because RateLimitMiddleware instantiates it in __init__
    with patch("app.middleware.rate_limiter.ConfigLoader") as mock_config_class:
        app.add_middleware(RateLimitMiddleware, token_bucket=mock_bucket)
        yield app, mock_bucket, mock_config_class.return_value

@pytest.fixture
def client(app_with_mocks):
    app, _, _ = app_with_mocks
    return TestClient(app)

def test_missing_api_key(client):
    response = client.get("/")
    assert response.status_code == 401
    assert response.text == "Unauthorized"

def test_allowed_request(app_with_mocks, client):
    app, mock_bucket, mock_config = app_with_mocks

    mock_rule = Rule(path="/", capacity=10.0, refill_rate=1.0)
    mock_config.get_rule.return_value = mock_rule
    mock_bucket.consume.return_value = (True, 9.0)

    response = client.get("/", headers={"X-API-Key": "test-key"})

    assert response.status_code == 200
    assert response.headers["X-RateLimit-Limit"] == "10.0"
    assert response.headers["X-RateLimit-Remaining"] == "9"
    assert "X-RateLimit-Reset" in response.headers
    assert int(response.headers["X-RateLimit-Reset"]) > 0

def test_rate_limited_request(app_with_mocks, client):
    app, mock_bucket, mock_config = app_with_mocks

    mock_rule = Rule(path="/", capacity=10.0, refill_rate=1.0)
    mock_config.get_rule.return_value = mock_rule
    mock_bucket.consume.return_value = (False, 0.0)

    response = client.get("/", headers={"X-API-Key": "test-key"})

    assert response.status_code == 429
    assert response.text == "Too Many Requests"
    assert response.headers["X-RateLimit-Limit"] == "10.0"
    assert response.headers["X-RateLimit-Remaining"] == "0"
    assert "Retry-After" in response.headers
    assert "X-RateLimit-Reset" in response.headers
    assert int(response.headers["X-RateLimit-Reset"]) > 0

def test_fail_open(app_with_mocks, client):
    app, mock_bucket, mock_config = app_with_mocks

    mock_rule = Rule(path="/", capacity=10.0, refill_rate=1.0)
    mock_config.get_rule.return_value = mock_rule
    mock_bucket.consume.side_effect = Exception("Redis down")

    response = client.get("/", headers={"X-API-Key": "test-key"})

    # Should fail open and allow the request
    assert response.status_code == 200
    assert response.text == "Hello World"

def test_no_rule_found(app_with_mocks, client):
    app, mock_bucket, mock_config = app_with_mocks

    mock_config.get_rule.side_effect = ValueError("No rule")

    response = client.get("/", headers={"X-API-Key": "test-key"})

    # Should allow request if no rule is found
    assert response.status_code == 200
    assert response.text == "Hello World"
