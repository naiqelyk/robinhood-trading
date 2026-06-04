from __future__ import annotations
from robinhood.models import TradeSignal, PortfolioState, RebalancePlan, PlannedOrder
from config import GuardrailsConfig


def build_rebalance_plan(
    signals: list[TradeSignal],
    portfolio: PortfolioState,
    cfg: GuardrailsConfig,
) -> RebalancePlan:
    """
    Converts TradeSignal list into a GuardRail-enforced RebalancePlan.
    All clipping and filtering happens here in plain Python — Claude never sees this step.
    """
    clips: list[str] = []
    current_symbols = {p.symbol for p in portfolio.positions}
    total_value = portfolio.total_value or 1.0  # avoid div/0 if portfolio is empty

    # Filter hold signals; sort buys by conviction descending
    actionable = [s for s in signals if s.action in ("buy", "sell")]
    actionable.sort(key=lambda s: s.conviction, reverse=True)

    # Filter by allow_sells / allow_buys
    if not cfg.allow_sells:
        removed = [s for s in actionable if s.action == "sell"]
        if removed:
            clips.append(f"allow_sells=false: dropped {[s.symbol for s in removed]}")
        actionable = [s for s in actionable if s.action != "sell"]

    if not cfg.allow_buys:
        removed = [s for s in actionable if s.action == "buy"]
        if removed:
            clips.append(f"allow_buys=false: dropped {[s.symbol for s in removed]}")
        actionable = [s for s in actionable if s.action != "buy"]

    orders: list[PlannedOrder] = []
    projected_positions = set(current_symbols)

    for signal in actionable:
        # Position limit: only count buy signals adding new positions
        if signal.action == "buy" and signal.symbol not in projected_positions:
            if len(projected_positions) >= cfg.max_positions:
                clips.append(
                    f"max_positions={cfg.max_positions}: dropped {signal.symbol} (buy)"
                )
                continue
            projected_positions.add(signal.symbol)

        # Drift threshold: skip rebalance if position weight is already close to target
        if signal.action == "buy":
            current_pos = next((p for p in portfolio.positions if p.symbol == signal.symbol), None)
            if current_pos:
                current_pct = current_pos.market_value / total_value
                drift = abs(current_pct - signal.target_pct_of_portfolio)
                if drift < cfg.min_rebalance_drift_pct:
                    clips.append(
                        f"drift {drift:.1%} < threshold {cfg.min_rebalance_drift_pct:.1%}: "
                        f"skipped {signal.symbol}"
                    )
                    continue

        # Cap per-position dollar amount
        dollar = signal.suggested_usd
        max_by_pct = total_value * cfg.max_position_pct
        if dollar > max_by_pct:
            clips.append(
                f"max_position_pct: clipped {signal.symbol} ${dollar:.0f} → ${max_by_pct:.0f}"
            )
            dollar = max_by_pct

        # Cap per-order hard limit
        if dollar > cfg.max_single_order_usd:
            clips.append(
                f"max_single_order_usd: clipped {signal.symbol} ${dollar:.0f} → ${cfg.max_single_order_usd:.0f}"
            )
            dollar = cfg.max_single_order_usd

        # Minimum position size
        if dollar < cfg.min_position_usd:
            clips.append(
                f"min_position_usd={cfg.min_position_usd}: dropped {signal.symbol} (${dollar:.2f})"
            )
            continue

        orders.append(PlannedOrder(
            symbol=signal.symbol,
            action=signal.action,
            dollar_amount=round(dollar, 2),
            rationale=signal.rationale,
        ))

    # Sort: sells first to free up cash
    orders.sort(key=lambda o: 0 if o.action == "sell" else 1)

    total_buy = sum(o.dollar_amount for o in orders if o.action == "buy")
    total_sell = sum(o.dollar_amount for o in orders if o.action == "sell")

    # Scale down if total buys exceed overall cap
    if total_buy > cfg.max_total_order_usd:
        scale = cfg.max_total_order_usd / total_buy
        clips.append(
            f"max_total_order_usd: scaled all buys by {scale:.2f}x "
            f"(${total_buy:.0f} → ${cfg.max_total_order_usd:.0f})"
        )
        scaled_orders = []
        for o in orders:
            if o.action == "buy":
                new_amount = round(o.dollar_amount * scale, 2)
                if new_amount >= cfg.min_position_usd:
                    scaled_orders.append(o.model_copy(update={"dollar_amount": new_amount}))
                else:
                    clips.append(
                        f"After scaling, {o.symbol} fell below min_position_usd; dropped"
                    )
            else:
                scaled_orders.append(o)
        orders = scaled_orders
        total_buy = sum(o.dollar_amount for o in orders if o.action == "buy")

    return RebalancePlan(
        orders=orders,
        total_buy_usd=total_buy,
        total_sell_usd=total_sell,
        guardrail_clips=clips,
    )
