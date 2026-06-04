from __future__ import annotations
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box
from robinhood.models import PortfolioState, TradeSignal, RebalancePlan, ExecutionSummary

console = Console()


def print_portfolio(portfolio: PortfolioState) -> None:
    table = Table(title="Current Portfolio", box=box.SIMPLE_HEAD)
    table.add_column("Symbol", style="cyan")
    table.add_column("Market Value", justify="right")
    table.add_column("Quantity", justify="right")
    table.add_column("Avg Cost", justify="right")
    table.add_column("P&L %", justify="right")

    for p in portfolio.positions:
        pnl_color = "green" if p.unrealized_pnl_pct >= 0 else "red"
        table.add_row(
            p.symbol,
            f"${p.market_value:,.2f}",
            f"{p.quantity:.4f}",
            f"${p.avg_cost:.2f}",
            f"[{pnl_color}]{p.unrealized_pnl_pct:+.1%}[/{pnl_color}]",
        )

    console.print(table)
    console.print(
        f"  Portfolio value: [bold]${portfolio.total_value:,.2f}[/bold]  "
        f"Buying power: [bold]${portfolio.buying_power:,.2f}[/bold]\n"
    )


def print_signals(signals: list[TradeSignal]) -> None:
    table = Table(title="Trade Signals", box=box.SIMPLE_HEAD)
    table.add_column("Symbol", style="cyan")
    table.add_column("Action", justify="center")
    table.add_column("Conviction", justify="right")
    table.add_column("Suggested $", justify="right")
    table.add_column("Target %", justify="right")
    table.add_column("Rationale")

    for s in signals:
        color = "green" if s.action == "buy" else ("red" if s.action == "sell" else "yellow")
        table.add_row(
            s.symbol,
            f"[{color}]{s.action.upper()}[/{color}]",
            f"{s.conviction:.0%}",
            f"${s.suggested_usd:,.0f}",
            f"{s.target_pct_of_portfolio:.0%}",
            s.rationale[:60] + ("…" if len(s.rationale) > 60 else ""),
        )

    console.print(table)


def print_plan(plan: RebalancePlan) -> None:
    table = Table(title="Rebalance Plan", box=box.SIMPLE_HEAD)
    table.add_column("Symbol", style="cyan")
    table.add_column("Action", justify="center")
    table.add_column("Dollar Amount", justify="right")
    table.add_column("Rationale")

    for o in plan.orders:
        color = "green" if o.action == "buy" else "red"
        table.add_row(
            o.symbol,
            f"[{color}]{o.action.upper()}[/{color}]",
            f"${o.dollar_amount:,.2f}",
            o.rationale[:60] + ("…" if len(o.rationale) > 60 else ""),
        )

    console.print(table)

    if plan.guardrail_clips:
        console.print("\n[yellow]Guardrail adjustments:[/yellow]")
        for clip in plan.guardrail_clips:
            console.print(f"  • {clip}")

    console.print(
        f"\n  Total buys: [green]${plan.total_buy_usd:,.2f}[/green]  "
        f"Total sells: [red]${plan.total_sell_usd:,.2f}[/red]\n"
    )


def confirm_execution() -> bool:
    response = console.input("[bold yellow]Proceed with execution? [y/N][/bold yellow] ").strip().lower()
    return response == "y"


def print_execution_summary(summary: ExecutionSummary) -> None:
    if summary.completed:
        table = Table(title="Completed Orders", box=box.SIMPLE_HEAD)
        table.add_column("Symbol", style="cyan")
        table.add_column("Action", justify="center")
        table.add_column("Amount", justify="right")
        table.add_column("Order ID")
        for o in summary.completed:
            color = "green" if o.action == "buy" else "red"
            table.add_row(
                o.symbol,
                f"[{color}]{o.action.upper()}[/{color}]",
                f"${o.dollar_amount:,.2f}",
                o.order_id,
            )
        console.print(table)

    if summary.skipped:
        console.print("\n[yellow]Skipped orders:[/yellow]")
        for s in summary.skipped:
            console.print(f"  • {s.symbol}: {s.reason}")

    if summary.errors:
        console.print("\n[red]Errors:[/red]")
        for e in summary.errors:
            console.print(f"  • {e}")
