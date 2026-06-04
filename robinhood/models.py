from __future__ import annotations
from typing import Literal, Optional
from pydantic import BaseModel, Field


class Position(BaseModel):
    symbol: str
    quantity: float
    market_value: float
    avg_cost: float
    unrealized_pnl_pct: float


class PortfolioState(BaseModel):
    account_id: str
    buying_power: float
    total_value: float
    positions: list[Position] = Field(default_factory=list)


class TradeSignal(BaseModel):
    symbol: str
    action: Literal["buy", "sell", "hold"]
    conviction: float = Field(ge=0.0, le=1.0)
    rationale: str
    target_pct_of_portfolio: float = Field(ge=0.0, le=1.0)
    suggested_usd: float = Field(ge=0.0)


class PlannedOrder(BaseModel):
    symbol: str
    action: Literal["buy", "sell"]
    dollar_amount: float
    rationale: str


class RebalancePlan(BaseModel):
    orders: list[PlannedOrder]
    total_buy_usd: float
    total_sell_usd: float
    guardrail_clips: list[str] = Field(default_factory=list)


class CompletedOrder(BaseModel):
    symbol: str
    action: str
    dollar_amount: float
    order_id: str


class SkippedOrder(BaseModel):
    symbol: str
    reason: str


class ExecutionSummary(BaseModel):
    completed: list[CompletedOrder] = Field(default_factory=list)
    skipped: list[SkippedOrder] = Field(default_factory=list)
    errors: list[dict] = Field(default_factory=list)
