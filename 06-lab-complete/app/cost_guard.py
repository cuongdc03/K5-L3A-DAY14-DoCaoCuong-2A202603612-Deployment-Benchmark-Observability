"""
Cost guard (tiêu chí 8).

  • Cộng dồn chi phí LLM (tính theo token) vào Redis, theo ngày và theo tháng (UTC)
  • Tổng chi phí của cả agent vượt DAILY_BUDGET_USD hoặc MONTHLY_BUDGET_USD → HTTP 402

Budget tính cho toàn agent (bảo vệ hoá đơn LLM), không theo từng user.
Key theo ngày/tháng nên sang ngày/tháng mới tự reset; TTL để key cũ tự bị xoá.
"""
import logging
from datetime import datetime, timezone

from fastapi import HTTPException

from app.config import settings
from app.redis_client import r

logger = logging.getLogger("agent.cost")

# Giá USD / 1K tokens (tham khảo gpt-4o-mini)
PRICE_INPUT_PER_1K = 0.00015
PRICE_OUTPUT_PER_1K = 0.0006

DAY_TTL_SECONDS = 2 * 24 * 3600
MONTH_TTL_SECONDS = 32 * 24 * 3600


def estimate_tokens(text: str) -> int:
    """Ước lượng thô: ~4 ký tự / token."""
    return max(1, round(len(text) / 4))


def estimate_cost(input_tokens: int, output_tokens: int) -> float:
    return input_tokens / 1000 * PRICE_INPUT_PER_1K + output_tokens / 1000 * PRICE_OUTPUT_PER_1K


def _keys() -> tuple[str, str]:
    now = datetime.now(timezone.utc)
    return f"cost:daily:{now:%Y-%m-%d}", f"cost:monthly:{now:%Y-%m}"


def check_budget(estimated_cost: float = 0.0) -> None:
    """Gọi TRƯỚC khi gọi LLM. Đã tiêu + ước tính request này vượt budget → 402."""
    day_key, month_key = _keys()
    spent_day, spent_month = (float(v or 0) for v in r.mget(day_key, month_key))
    for period, spent, budget in (("daily", spent_day, settings.daily_budget_usd),
                                  ("monthly", spent_month, settings.monthly_budget_usd)):
        if spent + estimated_cost > budget:
            logger.warning("budget exceeded", extra={"event": "budget_exceeded", "period": period,
                                                     "spent_usd": round(spent, 6), "budget_usd": budget})
            raise HTTPException(
                status_code=402,
                detail={"error": f"{period.capitalize()} budget exceeded",
                        "spent_usd": round(spent, 6), "budget_usd": budget},
            )


def record_cost(input_tokens: int, output_tokens: int) -> float:
    """Gọi SAU khi gọi LLM. Cộng chi phí của request vào tổng, trả về chi phí request."""
    cost = estimate_cost(input_tokens, output_tokens)
    day_key, month_key = _keys()
    pipe = r.pipeline(transaction=True)
    pipe.incrbyfloat(day_key, cost)
    pipe.expire(day_key, DAY_TTL_SECONDS)
    pipe.incrbyfloat(month_key, cost)
    pipe.expire(month_key, MONTH_TTL_SECONDS)
    pipe.execute()
    return cost
