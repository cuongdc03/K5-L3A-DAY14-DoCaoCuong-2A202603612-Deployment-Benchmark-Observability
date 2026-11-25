"""
API Key authentication (tiêu chí 6).

  • Client gửi header  X-API-Key: <key>
  • Key phải trùng biến môi trường AGENT_API_KEY
  • Thiếu hoặc sai key → HTTP 401
"""
import hmac
import logging

from fastapi import HTTPException, Security
from fastapi.security.api_key import APIKeyHeader

from app.config import settings

logger = logging.getLogger("agent.auth")

# auto_error=False: tự trả 401 thay vì để FastAPI trả 403/422 khi thiếu header
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

_UNAUTHORIZED = {"WWW-Authenticate": "ApiKey"}


def verify_api_key(api_key: str | None = Security(api_key_header)) -> str:
    """Dependency cho FastAPI — dùng trong /ask: Depends(verify_api_key)."""
    if not settings.agent_api_key:
        # Fail closed: chưa cấu hình key thì không cho ai vào
        logger.error("AGENT_API_KEY is not configured; rejecting request", extra={"event": "auth_misconfigured"})
        raise HTTPException(status_code=401, detail="Invalid or missing API key", headers=_UNAUTHORIZED)
    # compare_digest: so sánh thời gian hằng, tránh timing attack
    if not api_key or not hmac.compare_digest(api_key.encode(), settings.agent_api_key.encode()):
        raise HTTPException(status_code=401, detail="Invalid or missing API key", headers=_UNAUTHORIZED)
    return api_key
