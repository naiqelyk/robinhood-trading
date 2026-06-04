from __future__ import annotations
import json
import re
import anthropic
from robinhood.models import TradeSignal, PortfolioState
from pipeline.research import ResearchBundle
from config import ModelsConfig, GuardrailsConfig


ANALYST_SYSTEM = """You are a quantitative equity analyst. Analyze the provided research data and generate trade recommendations consistent with the user's strategy.

OUTPUT FORMAT: Respond with ONLY valid JSON — a list of TradeSignal objects with NO additional text or markdown fencing:
[
  {{
    "symbol": "AAPL",
    "action": "buy" | "sell" | "hold",
    "conviction": 0.0-1.0,
    "rationale": "brief one-sentence reason",
    "target_pct_of_portfolio": 0.0-1.0,
    "suggested_usd": 0.0
  }}
]

RULES:
- Only include symbols from the research data provided.
- action must be exactly "buy", "sell", or "hold" (lowercase).
- conviction is your confidence level (1.0 = very high, 0.0 = very low).
- target_pct_of_portfolio is the desired weight after rebalancing (0.10 = 10%).
- suggested_usd is the dollar amount to buy or sell (not the total position size).
- rationale must be a single concise sentence.
- Do not exceed max_positions or max_position_pct constraints.
- All signals that do not change positions (hold) should still be included for completeness.
"""


def analyze(
    client: anthropic.Anthropic,
    strategy_prompt: str,
    research_bundle: ResearchBundle,
    portfolio: PortfolioState,
    models_cfg: ModelsConfig,
    guardrails_cfg: GuardrailsConfig,
    console=None,
) -> list[TradeSignal]:
    system = ANALYST_SYSTEM + f"""
STRATEGY: {strategy_prompt}

CONSTRAINTS:
- Max positions: {guardrails_cfg.max_positions}
- Max single position: {guardrails_cfg.max_position_pct:.0%} of portfolio
- Current portfolio value: ${portfolio.total_value:,.2f}
- Current buying power: ${portfolio.buying_power:,.2f}
"""

    user_content = (
        f"CURRENT PORTFOLIO:\n{portfolio.model_dump_json(indent=2)}\n\n"
        f"RESEARCH DATA:\n{research_bundle.to_json(indent=2)}"
    )

    if console:
        with console.status(f"[bold]Analyzing with {models_cfg.analyst}…[/bold]"):
            response = client.messages.create(
                model=models_cfg.analyst,
                max_tokens=4096,
                system=system,
                messages=[{"role": "user", "content": user_content}],
            )
    else:
        response = client.messages.create(
            model=models_cfg.analyst,
            max_tokens=4096,
            system=system,
            messages=[{"role": "user", "content": user_content}],
        )

    raw = response.content[0].text.strip()
    m = re.search(r'```(?:\w+)?\s*\n?([\s\S]*?)\n?```', raw)
    if m:
        raw = m.group(1).strip()
    else:
        m = re.search(r'(\[[\s\S]*\])', raw)
        if m:
            raw = m.group(1).strip()

    signals_data = json.loads(raw)
    return [TradeSignal.model_validate(s) for s in signals_data]
