"""
Rate limiting (tiêu chí 7).

  • Mỗi API key tối đa RATE_LIMIT_PER_MINUTE request trong 60 giây (sliding window log)
  • Vượt giới hạn → HTTP 429 kèm header Retry-After
  • State lưu trong Redis (sorted set, score = timestamp ms) nên mọi worker / instance
    dùng chung và vẫn còn sau khi restart agent.

Cả bước dọn → đếm → thêm chạy trong một Lua script nên là atomic: nhiều worker
gửi cùng lúc cũng không vượt giới hạn.
"""
import hashlib
import math
import time
import uuid

from fastapi import HTTPException

from app.config import settings
from app.redis_client import r

WINDOW_MS = 60_000

_SLIDING_WINDOW_LUA = """
local key    = KEYS[1]
local now    = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit  = tonumber(ARGV[3])
redis.call('ZREMRANGEBYSCORE', key, '-inf', now - window)
local count = redis.call('ZCARD', key)
if count >= limit then
  local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
  return {0, tonumber(oldest[2])}
end
redis.call('ZADD', key, now, ARGV[4])
redis.call('PEXPIRE', key, window)
return {1, limit - count - 1}
"""
_sliding_window = r.register_script(_SLIDING_WINDOW_LUA)


def _bucket_key(api_key: str) -> str:
    # Không lưu API key dạng rõ trong Redis
    digest = hashlib.sha256(api_key.encode()).hexdigest()[:24]
    return f"ratelimit:{digest}"


def check_rate_limit(key: str) -> int:
    """Raise 429 nếu vượt giới hạn; trả về số request còn lại trong window."""
    limit = settings.rate_limit_per_minute
    now_ms = int(time.time() * 1000)
    member = f"{now_ms}-{uuid.uuid4().hex[:8]}"
    allowed, value = _sliding_window(keys=[_bucket_key(key)], args=[now_ms, WINDOW_MS, limit, member])
    if int(allowed) == 1:
        return int(value)

    oldest_ms = int(value)
    retry_after = max(1, math.ceil((oldest_ms + WINDOW_MS - now_ms) / 1000))
    raise HTTPException(
        status_code=429,
        detail={"error": "Rate limit exceeded", "limit_per_minute": limit, "retry_after_seconds": retry_after},
        headers={
            "Retry-After": str(retry_after),
            "X-RateLimit-Limit": str(limit),
            "X-RateLimit-Remaining": "0",
        },
    )
