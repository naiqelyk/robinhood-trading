from __future__ import annotations
import os
from pathlib import Path
from typing import Literal, Optional
import yaml
from pydantic import BaseModel, Field
from dotenv import load_dotenv

load_dotenv()


class AccountConfig(BaseModel):
    account_id: str


class ModelsConfig(BaseModel):
    analyst: str = "claude-opus-4-8"
    executor: str = "claude-sonnet-4-6"


class CandidateUniverse(BaseModel):
    method: Literal["static", "search"] = "static"
    static_symbols: list[str] = Field(default_factory=list)
    search_queries: list[str] = Field(default_factory=list)


class ResearchConfig(BaseModel):
    candidate_universe: CandidateUniverse = Field(default_factory=CandidateUniverse)
    news_articles_per_symbol: int = 5
    fundamental_lookback_days: int = 365


class GuardrailsConfig(BaseModel):
    max_positions: int = 10
    max_position_pct: float = 0.20
    max_single_order_usd: float = 5000.0
    max_total_order_usd: float = 20000.0
    min_position_usd: float = 50.0
    min_rebalance_drift_pct: float = 0.03
    allow_sells: bool = True
    allow_buys: bool = True
    require_confirmation: bool = True
    dry_run: bool = False


class ExecutionConfig(BaseModel):
    max_executor_iterations: int = 20


class Config(BaseModel):
    account: AccountConfig
    models: ModelsConfig = Field(default_factory=ModelsConfig)
    research: ResearchConfig = Field(default_factory=ResearchConfig)
    guardrails: GuardrailsConfig = Field(default_factory=GuardrailsConfig)
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)


def load_config(path: str = "config.yaml") -> Config:
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    with open(config_path) as f:
        data = yaml.safe_load(f)
    return Config.model_validate(data)


def require_env(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise EnvironmentError(
            f"Required environment variable {name} is not set.\n"
            f"Add it to your shell or a .env file in the project directory."
        )
    return val
