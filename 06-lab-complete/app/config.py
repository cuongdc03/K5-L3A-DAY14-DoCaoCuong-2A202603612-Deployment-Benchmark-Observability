"""
Config — 12-Factor: mọi cấu hình đọc từ biến môi trường.

Giá trị mặc định chỉ dành cho chạy local; trên cloud đặt biến môi trường trên platform.
Secret (AGENT_API_KEY) không có default: thiếu thì /ask từ chối mọi request,
và khi ENVIRONMENT=production thì app không khởi động (fail fast).
"""
import os
from dataclasses import dataclass, field


def _env_int(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


def _env_float(name: str, default: float) -> float:
    return float(os.getenv(name, str(default)))


@dataclass(frozen=True)
class Settings:
    app_name: str = field(default_factory=lambda: os.getenv("APP_NAME", "production-ai-agent"))
    app_version: str = field(default_factory=lambda: os.getenv("APP_VERSION", "1.0.0"))
    port: int = field(default_factory=lambda: _env_int("PORT", 8000))
    environment: str = field(default_factory=lambda: os.getenv("ENVIRONMENT", "development"))
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO").upper())

    agent_api_key: str = field(default_factory=lambda: os.getenv("AGENT_API_KEY", ""))
    redis_url: str = field(default_factory=lambda: os.getenv("REDIS_URL", "redis://localhost:6379/0"))

    rate_limit_per_minute: int = field(default_factory=lambda: _env_int("RATE_LIMIT_PER_MINUTE", 10))
    daily_budget_usd: float = field(default_factory=lambda: _env_float("DAILY_BUDGET_USD", 1.0))
    monthly_budget_usd: float = field(default_factory=lambda: _env_float("MONTHLY_BUDGET_USD", 10.0))

    def validate(self) -> "Settings":
        if self.environment == "production" and not self.agent_api_key:
            raise RuntimeError("AGENT_API_KEY must be set when ENVIRONMENT=production")
        if self.rate_limit_per_minute < 1:
            raise RuntimeError("RATE_LIMIT_PER_MINUTE must be >= 1")
        if self.daily_budget_usd <= 0 or self.monthly_budget_usd <= 0:
            raise RuntimeError("DAILY_BUDGET_USD and MONTHLY_BUDGET_USD must be > 0")
        return self


settings = Settings().validate()
