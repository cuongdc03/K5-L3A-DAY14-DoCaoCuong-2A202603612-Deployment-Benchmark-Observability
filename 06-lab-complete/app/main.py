"""
Production AI Agent — Part 6 Final Project.

Chạy:   uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 2
Chấm:   python check_production_ready.py
"""
import json
import logging
import sys
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import redis
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.auth import verify_api_key
from app.config import settings
from app.cost_guard import check_budget, estimate_cost, estimate_tokens, record_cost
from app.rate_limiter import check_rate_limit
from app.redis_client import r, redis_healthy
from utils.mock_llm import ask as llm_ask


# ─────────────────────────────────────────────────────────
# Structured logging — mỗi dòng log là 1 JSON object
# ─────────────────────────────────────────────────────────
_STD_ATTRS = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(timespec="milliseconds"),
            "lvl": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        # Field truyền qua extra={...}
        for k, v in record.__dict__.items():
            if k not in _STD_ATTRS and not k.startswith("_"):
                entry[k] = v
        if record.exc_info:
            entry["exc_type"] = record.exc_info[0].__name__ if record.exc_info[0] else None
        return json.dumps(entry, ensure_ascii=False, default=str)


def _setup_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(settings.log_level)
    # Log của uvicorn trong worker cũng ra JSON; access log thay bằng log request của app
    for name in ("uvicorn", "uvicorn.error"):
        lg = logging.getLogger(name)
        lg.handlers[:] = []
        lg.propagate = True
    logging.getLogger("uvicorn.access").disabled = True


_setup_logging()
logger = logging.getLogger("agent")

START_TIME = time.time()
_state = {"shutting_down": False}


# ─────────────────────────────────────────────────────────
# Lifecycle — startup / graceful shutdown
# ─────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("startup", extra={"event": "startup", "version": settings.app_version,
                                  "environment": settings.environment, "port": settings.port,
                                  "redis_ok": redis_healthy()})
    yield
    # uvicorn nhận SIGTERM → ngừng nhận kết nối mới → chờ request đang chạy xong
    # (tối đa --timeout-graceful-shutdown) → chạy phần này → process thoát exit code 0
    _state["shutting_down"] = True
    logger.info("shutdown: closing connections", extra={"event": "shutdown"})
    try:
        r.close()
        r.connection_pool.disconnect()
    except Exception:
        pass
    logger.info("shutdown complete", extra={"event": "shutdown_complete"})


app = FastAPI(title="Production AI Agent", version=settings.app_version, lifespan=lifespan,
              docs_url=None if settings.environment == "production" else "/docs",
              redoc_url=None, openapi_url=None if settings.environment == "production" else "/openapi.json")


@app.middleware("http")
async def request_log(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
    start = time.perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        response.headers["X-Request-ID"] = request_id
        return response
    finally:
        # Không log header (có API key) hay nội dung câu hỏi
        logger.info("request", extra={"event": "request", "request_id": request_id,
                                      "method": request.method, "path": request.url.path,
                                      "status": status, "ms": round((time.perf_counter() - start) * 1000, 1)})


# ─────────────────────────────────────────────────────────
# Error handling — mã lỗi rõ ràng, không lộ stack trace
# ─────────────────────────────────────────────────────────
@app.exception_handler(redis.RedisError)
async def redis_error_handler(request: Request, exc: redis.RedisError):
    logger.error("redis unavailable", extra={"event": "redis_error", "path": request.url.path,
                                             "error": type(exc).__name__})
    return JSONResponse(status_code=503, content={"detail": "Service temporarily unavailable"},
                        headers={"Retry-After": "5"})


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, exc: Exception):
    logger.error("unhandled error", extra={"event": "unhandled_error", "path": request.url.path,
                                           "error": type(exc).__name__})
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


# ─────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────
class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    user_id: str | None = Field(default=None, max_length=128)


@app.get("/")
def root():
    return {"app": settings.app_name, "version": settings.app_version, "docs": "/docs"}


@app.get("/health")
def health():
    """Liveness: process còn chạy là 200, không phụ thuộc Redis."""
    return {
        "status": "ok",
        "version": settings.app_version,
        "environment": settings.environment,
        "uptime_seconds": round(time.time() - START_TIME, 1),
    }


@app.get("/ready")
def ready():
    """Readiness: 503 khi đang shutdown hoặc Redis không kết nối được."""
    if _state["shutting_down"]:
        raise HTTPException(status_code=503, detail="Shutting down")
    if not redis_healthy():
        raise HTTPException(status_code=503, detail="Redis unavailable")
    return {"status": "ready"}


@app.post("/ask")
def ask(body: AskRequest, api_key: str = Depends(verify_api_key)):
    # def (không async): FastAPI chạy trong threadpool, LLM chậm không block event loop
    remaining = check_rate_limit(api_key)                       # 429
    input_tokens = estimate_tokens(body.question)
    check_budget(estimate_cost(input_tokens, 0))                # 402

    answer = llm_ask(body.question)

    output_tokens = estimate_tokens(answer)
    cost = record_cost(input_tokens, output_tokens)
    logger.info("llm call", extra={"event": "llm_call", "input_tokens": input_tokens,
                                   "output_tokens": output_tokens, "cost_usd": round(cost, 8)})
    return {
        "question": body.question,
        "answer": answer,
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_usd": round(cost, 8),
            "requests_remaining": remaining,
        },
    }
