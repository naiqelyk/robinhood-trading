# Cozy Hare — Agentic Trading Bot

A Python CLI trading bot that researches stocks, generates trade signals from a natural language strategy, and executes orders through Robinhood.

---

## Architecture

The bot runs a structured three-phase pipeline. Python orchestrates each phase; Claude acts as a reasoning engine in phases 1–2 and as an agentic executor with live Robinhood tool access in phase 3. Safety guardrails are enforced in plain Python code between analysis and execution — Claude never bypasses them.

```
Claude Code (orchestrator)
  │
  ├─ Phase 0: Portfolio Snapshot   Claude Code fetches via Robinhood MCP tools
  │                                (get_accounts → get_portfolio + get_equity_positions)
  │
  ├─ bot.py --portfolio '...' --strategy "..."
  │    │
  │    ├─ Phase 1: Research        yfinance + Tavily web/news search
  │    │                           Fundamentals, price technicals, recent news,
  │    │                           analyst ratings for each candidate symbol
  │    │
  │    ├─ Phase 2: Analysis        Claude (no tools) → structured JSON
  │    │                           Reads research + portfolio, applies strategy,
  │    │                           returns TradeSignals
  │    │
  │    ├─ Guardrail Check          Pure Python — enforces position limits,
  │    │                           dollar caps, drift thresholds, sell/buy flags
  │    │
  │    ├─ Confirmation             Rich terminal table + [y/N] prompt
  │    │
  │    └─ Outputs plan JSON        Printed to stdout for Claude Code to execute
  │
  └─ Phase 3: Execution            Claude Code executes via Robinhood MCP tools
                                   (review_equity_order → place_equity_order)
                                   Sells first, review required before every place
```

### How the Robinhood connection works

All Robinhood I/O goes through Claude Code's authenticated MCP tools (`mcp__claude_ai_Robinhood__*`), connected via your claude.ai session. No bearer token or separate auth is needed. The Python bot handles only the logic layers — research, analysis, guardrails — and has no direct Robinhood dependency.

### File structure

```
trading-bot/
├── bot.py                  # CLI entry point — research, analysis, guardrails, plan output
├── config.yaml             # User-editable settings (limits, models, universe)
├── config.py               # Pydantic config loader + env var helpers
├── guardrails.py           # All safety enforcement — position sizing, caps, filters
├── display.py              # Rich terminal tables and confirmation prompts
├── requirements.txt
├── pipeline/
│   ├── research.py         # yfinance fundamentals + Tavily news/finance search
│   └── analyst.py          # Claude analysis call (no tools) → TradeSignal list
└── robinhood/
    └── models.py           # Pydantic models: PortfolioState, TradeSignal, RebalancePlan, etc.
```

---

## Setup

### 1. Prerequisites

- Python 3.9+
- Claude Code with the **claude.ai Robinhood connector** active — connect it at [claude.ai](https://claude.ai) under MCP integrations (no manual `claude mcp add` needed)
- An Anthropic API key — [console.anthropic.com](https://console.anthropic.com)
- *(Optional)* A Tavily API key for news/analyst research — [tavily.com](https://tavily.com) (free tier available)

### 2. Install dependencies

```bash
cd ~/Claude/trading-bot
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Set environment variables

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
export TAVILY_API_KEY="tvly-..."          # optional but recommended
```

Add these to your `~/.zshrc` or `~/.bashrc` to persist them.

### 4. Run the bot via Claude Code

The bot is orchestrated through Claude Code, which handles Robinhood authentication. Ask Claude Code to run a trading strategy — it will fetch your portfolio, call `bot.py` with the data, and execute the resulting plan.

Example prompt to Claude Code:
> "Run the trading bot with strategy: buy momentum tech stocks, max 5 positions, no single position > 20%"

---

## Usage

### Basic run (via Claude Code)

Ask Claude Code:
> "Run the trading bot with strategy: [your strategy]"

Claude Code will:
1. Fetch your current portfolio via Robinhood MCP tools
2. Call `python3 bot.py` with the portfolio and strategy
3. Research candidate symbols
4. Generate trade signals via Claude
5. Show you a proposed trading plan
6. Ask for confirmation, then execute orders via Robinhood MCP tools

### Direct CLI (when you already have portfolio JSON)

```bash
python3 bot.py \
  --portfolio '{"account_id":"417226214","buying_power":90.0,"total_value":99.98,"positions":[...]}' \
  --strategy "Buy momentum tech stocks, max 5 positions, no single position > 20%"
```

### Dry run (no orders placed)

```bash
python3 bot.py --portfolio '...' --strategy "Trade into equal-weight mag7" --dry-run
```

Runs the full pipeline including guardrail checks, prints the proposed plan, then exits without executing.

### Research only (signals, no execution)

```bash
python3 bot.py --portfolio '...' --strategy "Value stocks with P/E < 15" --symbols AAPL MSFT GOOGL --research-only
```

Stops after analysis and prints trade signals. Useful for evaluating strategy quality before committing.

### Restrict the candidate universe

```bash
python3 bot.py --portfolio '...' --strategy "Buy the dip in semiconductors" --symbols NVDA AMD INTC ASML TSM
```

Without `--symbols`, the universe comes from `config.yaml`. Current holdings are always included.

### Skip the confirmation prompt

```bash
python3 bot.py --portfolio '...' --strategy "Trim positions that are up > 20%" --no-confirm
```

### Write an audit log

```bash
python3 bot.py --portfolio '...' --strategy "..." --log-file ~/trades.log
```

Appends a JSON entry per run covering: strategy, portfolio before, signals, guardrail clips, and plan.

### Read strategy from a file

```bash
echo "Buy high-momentum ETFs with strong 3-month performance, limit to 3 positions" > strategy.txt
python3 bot.py --portfolio '...' --strategy-file strategy.txt --dry-run
```

---

## Configuration

Edit `config.yaml` to change defaults:

```yaml
account:
  account_id: "417226214"       # Must be an agentic_allowed=true Robinhood account

models:
  analyst: "claude-opus-4-8"    # Used for research analysis (no tools)
  executor: "claude-sonnet-4-6" # Used for trade execution (with MCP tools)

research:
  candidate_universe:
    method: "static"            # "static" = use list below; "search" = Robinhood search
    static_symbols:             # Symbols to always research
      - SPY
      - QQQ
      - NVDA

guardrails:
  max_positions: 10             # Hard cap on open positions
  max_position_pct: 0.20        # No single position > 20% of portfolio
  max_single_order_usd: 5000    # Per-order dollar cap
  max_total_order_usd: 20000    # Total deployment cap per run
  min_position_usd: 50          # Drop orders smaller than this
  min_rebalance_drift_pct: 0.03 # Skip trade if position weight drifts < 3% from target
  allow_sells: true             # Set false to make the bot buy-only
  allow_buys: true              # Set false to make the bot sell-only
  require_confirmation: true    # Prompt before execution
  dry_run: false                # Set true for permanent paper-trading mode
```

All `guardrails` values can also be overridden per-run via CLI flags:

```bash
python3 bot.py --strategy "..." --max-positions 3 --max-position-pct 0.15
```

---

## Guardrails

All limits are enforced in Python before Claude ever sees the execution plan. Claude cannot override them.

| Guardrail | Default | What it does |
|---|---|---|
| `max_positions` | 10 | Drops lowest-conviction buys beyond this count |
| `max_position_pct` | 20% | Clips any single order to `portfolio_value × 0.20` |
| `max_single_order_usd` | $5,000 | Hard per-order cap |
| `max_total_order_usd` | $20,000 | Scales all buys down proportionally if exceeded |
| `min_position_usd` | $50 | Drops orders below Robinhood's practical minimum |
| `min_rebalance_drift_pct` | 3% | Skips trading a position already near its target weight |
| `allow_sells` / `allow_buys` | both on | Toggle to restrict direction |
| `require_confirmation` | on | Interactive `[y/N]` prompt before any order |
| `dry_run` | off | Shows plan only; execution phase is never reached |
| `review_required` | hardcoded on | `review_equity_order` is always called before `place_equity_order` |

Sells are always executed before buys to free up cash first.

---

## Example strategies

```
"Buy momentum tech stocks — prioritize names above their 50-day MA with 
positive earnings growth. Max 5 positions, no single position > 15%."

"Trade my portfolio to equal weight across current holdings."

"Trim any position that has drifted above 25% of my portfolio back to 20%."

"Research NVDA, AAPL, MSFT, AMZN and recommend the strongest buy given 
current valuations and recent news."

"Sell my weakest-performing position and redeploy into the strongest 
momentum name from this list: SPY, QQQ, IWM."
```

---

## Troubleshooting

**Robinhood tools not available**
Ensure the claude.ai Robinhood connector is connected under MCP integrations at [claude.ai](https://claude.ai). The `mcp__claude_ai_Robinhood__*` tools must be present in your Claude Code session. These tools handle all portfolio fetching and order execution — the Python bot has no direct Robinhood dependency.

**`No candidate symbols to research`**
Add symbols via `--symbols` or set `static_symbols` in `config.yaml`.

**`Analysis failed: json.JSONDecodeError`**
Claude occasionally returns malformed JSON under heavy load. Re-run the command — the analyst prompt includes explicit JSON-only instructions and this is rare.

**Tavily not returning news**
The bot works without `TAVILY_API_KEY` — research falls back to fundamentals only. Set the key to enable news and analyst data.
