import time
import redis.asyncio as redis
from typing import Tuple

class TokenBucket:
    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client
        with open("app/limiter/lua_scripts/token_bucket.lua", "r") as f:
            self.script = self.redis.register_script(f.read())

    async def consume(self, api_key: str, path: str, capacity: float, refill_rate: float) -> Tuple[bool, float]:
        key = f"ratelimit:{api_key}:{path}"
        now = time.time()
        # Lua script returns {allowed, tokens}
        res = await self.script(keys=[key], args=[capacity, refill_rate, now])
        return bool(res[0]), float(res[1])
