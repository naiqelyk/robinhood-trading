#!/usr/bin/env python3
"""Cozy Hare — Agentic Trading Bot"""
from __future__ import annotations
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import anthropic
from rich.console import Console

from config import load_config, require_env, Config
from guardrails import build_rebalance_plan
from display import (
    console,
    print_portfolio,
    print_signals,
    print_plan,
    confirm_execution,
    print_execution_summary,
)
from pipeline.research import research_symbols, discover_symbols
from pipeline.analyst import analyze
from pipeline.executor import get_portfolio_state, execute_plan
from robinhood.auth import get_robinhood_mcp_token, clear_cached_token


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="bot.py",
        description="Cozy Hare — Agentic Trading Bot",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 bot.py --strategy "Buy momentum tech stocks, max 5 positions"
  python3 bot.py --strategy "..." --dry-run
  python3 bot.py --strategy "..." --research-only
  python3 bot.py --test-connection
  python3 bot.py --strategy "..." --symbols NVDA MSFT AAPL
  python3 bot.py --reauth

Environment variables:
  ANTHROPIC_API_KEY     Required — Anthropic API key
  TAVILY_API_KEY        Optional — enables news/finance research
  ROBINHOOD_MCP_TOKEN   Optional — override cached Robinhood token
""",
    )

    strategy_group = parser.add_argument_group("Strategy")
    strategy_group.add_argument("--strategy", metavar="PROMPT", help="Natural language strategy")
    strategy_group.add_argument("--strategy-file", metavar="PATH", help="Read strategy from file")

    parser.add_argument("--config", default="config.yaml", metavar="PATH", help="Config file path")
    parser.add_argument("--dry-run", action="store_true", help="Run pipeline but skip order execution")
    parser.add_argument("--no-confirm", action="store_true", help="Skip interactive confirmation")
    parser.add_argument("--research-only", action="store_true", help="Stop after analysis; print signals")
    parser.add_argument("--test-connection", action="store_true", help="Test Robinhood MCP connection and exit")
    parser.add_argument("--reauth", action="store_true", help="Clear cached token and re-run OAuth")
    parser.add_argument("--symbols", nargs="+", metavar="SYM", help="Restrict candidate universe")
    parser.add_argument("--max-positions", type=int, metavar="N")
    parser.add_argument("--max-position-pct", type=float, metavar="PCT")
    parser.add_argument("--log-file", metavar="PATH", help="Append JSON audit log")

    return parser.parse_args()


def resolve_strategy(args: argparse.Namespace) -> str:
    if args.strategy_file:
        path = Path(args.strategy_file)
        if not path.exists():
            console.print(f"[red]Strategy file not found: {args.strategy_file}[/red]")
            sys.exit(1)
        return path.read_text().strip()
    if args.strategy:
        return args.strategy
    console.print("[red]--strategy or --strategy-file is required.[/red]")
    sys.exit(1)


def resolve_symbols(args: argparse.Namespace, cfg: Config, current_symbols: list[str]) -> list[str]:
    if args.symbols:
        return list(set(args.symbols) | set(current_symbols))
    if cfg.research.candidate_universe.method == "static":
        static = cfg.research.candidate_universe.static_symbols
        return list(set(static) | set(current_symbols)) if static else current_symbols
    return current_symbols


def apply_cli_overrides(cfg: Config, args: argparse.Namespace) -> None:
    if args.max_positions is not None:
        cfg.guardrails.max_positions = args.max_positions
    if args.max_position_pct is not None:
        cfg.guardrails.max_position_pct = args.max_position_pct
    if args.dry_run:
        cfg.guardrails.dry_run = True
    if args.no_confirm:
        cfg.guardrails.require_confirmation = False


def write_audit_log(log_file: str, entry: dict) -> None:
    path = Path(log_file)
    existing = []
    if path.exists():
        try:
            existing = json.loads(path.read_text())
        except Exception:
            existing = []
    existing.append(entry)
    path.write_text(json.dumps(existing, indent=2, default=str))


def main() -> None:
    args = parse_args()

    if args.reauth:
        clear_cached_token()
        console.print("[yellow]Cached token cleared.[/yellow]")

    cfg = load_config(args.config)
    apply_cli_overrides(cfg, args)

    anthropic_key = require_env("ANTHROPIC_API_KEY")
    client = anthropic.Anthropic(api_key=anthropic_key)

    # Resolve token — triggers browser OAuth on first use
    try:
        token = get_robinhood_mcp_token()
    except Exception as e:
        console.print(f"[red]Auth error:[/red] {e}")
        sys.exit(1)

    # ── Test connection mode ──────────────────────────────────────────────────
    if args.test_connection:
        console.print("[bold]Testing Robinhood MCP connection…[/bold]")
        try:
            portfolio = get_portfolio_state(client, cfg.models.executor, token)
            console.print(f"[green]Connected.[/green] Account: {portfolio.account_id}")
            print_portfolio(portfolio)
        except Exception as e:
            console.print(f"[red]Connection failed:[/red] {e}")
            console.print("[dim]If the token expired, run: python3 bot.py --reauth[/dim]")
            sys.exit(1)
        return

    strategy = resolve_strategy(args)
    audit: dict = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "strategy": strategy,
        "dry_run": cfg.guardrails.dry_run,
    }

    # ── Phase 0: Portfolio snapshot ───────────────────────────────────────────
    console.rule("[bold blue]Phase 0 — Portfolio Snapshot[/bold blue]")
    try:
        with console.status("[bold]Fetching portfolio…[/bold]") as status:
            portfolio = get_portfolio_state(
                client, cfg.models.executor, token,
                on_status=lambda msg: status.update(f"[bold]Portfolio snapshot — {msg}[/bold]"),
            )
    except Exception as e:
        console.print(f"[red]Failed to fetch portfolio:[/red] {e}")
        console.print("[dim]If the token expired, run: python3 bot.py --reauth[/dim]")
        sys.exit(1)

    print_portfolio(portfolio)
    audit["portfolio_before"] = portfolio.model_dump()

    current_symbols = [p.symbol for p in portfolio.positions]
    candidate_symbols = resolve_symbols(args, cfg, current_symbols)

    # ── Discovery: external signals from Reddit & news ────────────────────────
    if cfg.research.discovery_max_symbols > 0:
        console.rule("[bold blue]Discovery — Reddit & News[/bold blue]")
        with console.status("[bold]Searching Reddit and financial news for trending stocks…[/bold]"):
            discovered = discover_symbols(
                strategy_hint=strategy,
                max_symbols=cfg.research.discovery_max_symbols,
                console=console,
            )
        if discovered:
            new_finds = [s for s in discovered if s not in candidate_symbols]
            if new_finds:
                console.print(f"Discovered [green]{len(new_finds)}[/green] new symbols: {', '.join(new_finds)}")
            candidate_symbols = list(dict.fromkeys(candidate_symbols + discovered))
        else:
            console.print("[dim]No symbols discovered (TAVILY_API_KEY not set or no results).[/dim]")

    if not candidate_symbols:
        console.print("[yellow]No candidate symbols to research. Add symbols via --symbols or config.yaml.[/yellow]")
        sys.exit(0)

    # ── Phase 1: Research ─────────────────────────────────────────────────────
    console.rule("[bold blue]Phase 1 — Research[/bold blue]")
    console.print(f"Researching {len(candidate_symbols)} symbols: {', '.join(candidate_symbols)}")
    research_bundle = research_symbols(
        candidate_symbols,
        max_articles=cfg.research.news_articles_per_symbol,
        console=console,
    )

    # ── Phase 2: Analysis ─────────────────────────────────────────────────────
    console.rule("[bold blue]Phase 2 — Analysis[/bold blue]")
    console.print(f"Strategy: [italic]{strategy}[/italic]\n")
    try:
        signals = analyze(client, strategy, research_bundle, portfolio, cfg.models, cfg.guardrails, console=console)
    except Exception as e:
        console.print(f"[red]Analysis failed:[/red] {e}")
        sys.exit(1)

    print_signals(signals)
    audit["signals"] = [s.model_dump() for s in signals]

    if args.research_only:
        console.print("\n[yellow]--research-only: stopping before execution.[/yellow]")
        if args.log_file:
            write_audit_log(args.log_file, audit)
        return

    # ── Guardrail check ───────────────────────────────────────────────────────
    console.rule("[bold blue]Guardrails[/bold blue]")
    plan = build_rebalance_plan(signals, portfolio, cfg.guardrails)
    print_plan(plan)
    audit["guardrail_clips"] = plan.guardrail_clips
    audit["plan"] = plan.model_dump()

    if not plan.orders:
        console.print("[yellow]No actionable orders after guardrail checks.[/yellow]")
        if args.log_file:
            write_audit_log(args.log_file, audit)
        return

    if cfg.guardrails.dry_run:
        console.print("\n[yellow]--dry-run: plan shown above, no orders placed.[/yellow]")
        if args.log_file:
            write_audit_log(args.log_file, audit)
        return

    # ── Confirmation ──────────────────────────────────────────────────────────
    if cfg.guardrails.require_confirmation:
        if not confirm_execution():
            console.print("[yellow]Aborted.[/yellow]")
            return

    # ── Phase 3: Execution ────────────────────────────────────────────────────
    console.rule("[bold blue]Phase 3 — Execution[/bold blue]")
    try:
        with console.status("[bold]Executing orders…[/bold]") as status:
            summary = execute_plan(
                client,
                cfg.models.executor,
                token,
                plan,
                cfg.account.account_id,
                max_iterations=cfg.execution.max_executor_iterations,
                on_status=lambda msg: status.update(f"[bold]Execution — {msg}[/bold]"),
            )
    except Exception as e:
        console.print(f"[red]Execution error:[/red] {e}")
        sys.exit(1)

    print_execution_summary(summary)
    audit["execution_summary"] = summary.model_dump()

    if args.log_file:
        write_audit_log(args.log_file, audit)
        console.print(f"\nAudit log written to [cyan]{args.log_file}[/cyan]")


if __name__ == "__main__":
    main()
