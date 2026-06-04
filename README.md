# Cozy Hare — Agentic Trading Bot

A Python CLI trading bot that researches stocks, generates trade signals from a natural language strategy, and executes rebalancing orders through Robinhood.

---

## Architecture

The bot runs a structured three-phase pipeline. Python orchestrates each phase; Claude acts as a reasoning engine in phases 1–2 and as an agentic executor with live Robinhood tool access in phase 3. Safety guardrails are enforced in plain Python code between analysis and execution — Claude never bypasses them.

```
bot.py --strategy "..."
  │
  ├─ Phase 0: Portfolio Snapshot   Claude + read-only Robinhood MCP tools
  │                                Fetches current positions and buying power
  │                                Spinner shows turn number and MCP tool calls
  │
  ├─ Phase 1: Research             yfinance + Tavily web/news search
  │                                Fundamentals, price technicals, recent news,
  │                                analyst ratings for each candidate symbol
  │                                Spinner per symbol (fundamentals → news);
  │                                ✓ checkmark on completion
  │
  ├─ Phase 2: Analysis             Claude (no tools) → structured JSON
  │                                Reads research + current portfolio,
  │                                applies your strategy, returns TradeSignals
  │                                Spinner shows model name while waiting
  │
  ├─ Guardrail Check               Pure Python — enforces position limits,
  │                                dollar caps, drift thresholds, sell/buy flags
  │
  ├─ Confirmation                  Rich terminal table + [y/N] prompt
  │                                (skippable with --no-confirm)
  │
  └─ Phase 3: Execution            Claude + Robinhood MCP write tools
                                   Sells first, calls review before every place,
                                   outputs structured execution summary
                                   Spinner shows turn number and MCP tool calls
```

### How the Robinhood connection works

The bot uses the Anthropic SDK's native MCP connector (`betas=["mcp-client-2025-11-20"]`). When Claude needs to call a Robinhood tool, the Anthropic API executes it server-side against `https://agent.robinhood.com/mcp/trading` — your Python process never implements an MCP client. The auth token is read from `ROBINHOOD_MCP_TOKEN` or from `~/.claude.json` (set automatically when you added the MCP server to Claude Code).

### File structure

```
trading-bot/
├── bot.py                  # CLI entry point and top-level orchestration
├── config.yaml             # User-editable settings (limits, models, universe)
├── config.py               # Pydantic config loader + env var helpers
├── guardrails.py           # All safety enforcement — position sizing, caps, filters
├── display.py              # Rich terminal tables and confirmation prompts
├── requirements.txt
├── pipeline/
│   ├── research.py         # yfinance fundamentals + Tavily news/finance search
│   ├── analyst.py          # Claude analysis call (no tools) → TradeSignal list
│   └── executor.py         # Agentic MCP loop — portfolio snapshot + trade execution
└── robinhood/
    ├── auth.py             # Resolves Robinhood MCP Bearer token
    └── models.py           # Pydantic models: PortfolioState, TradeSignal, RebalancePlan, etc.
```

---

## Setup

### 1. Prerequisites

- Python 3.9+
- Claude Code with the Robinhood MCP configured:
  ```bash
  claude mcp add robinhood-trading --transport http https://agent.robinhood.com/mcp/trading
  ```
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
export ROBINHOOD_MCP_TOKEN="..."          # optional — auto-read from ~/.claude.json
```

Add these to your `~/.zshrc` or `~/.bashrc` to persist them.

### 4. Test the connection

```bash
python3 bot.py --test-connection
```

This runs only the read-only portfolio snapshot and confirms that the Robinhood MCP auth is working. No trades are placed.

---

## Usage

### Basic run

```bash
python3 bot.py --strategy "Buy momentum tech stocks, max 5 positions, no single position > 20%"
```

The bot will:
1. Fetch your current portfolio
2. Research candidate symbols
3. Generate trade signals via Claude
4. Show you a proposed rebalance plan
5. Ask for confirmation before placing any orders

### Dry run (no orders placed)

```bash
python3 bot.py --strategy "Rebalance to equal-weight mag7" --dry-run
```

Runs the full pipeline including guardrail checks, prints the proposed plan, then exits without executing.

### Research only (signals, no execution)

```bash
python3 bot.py --strategy "Value stocks with P/E < 15" --symbols AAPL MSFT GOOGL --research-only
```

Stops after analysis and prints trade signals. Useful for evaluating strategy quality before committing.

### Restrict the candidate universe

```bash
python3 bot.py --strategy "Buy the dip in semiconductors" --symbols NVDA AMD INTC ASML TSM
```

Without `--symbols`, the universe comes from `config.yaml`. Current holdings are always included.

### Skip the confirmation prompt

```bash
python3 bot.py --strategy "Trim positions that are up > 20%" --no-confirm
```

### Write an audit log

```bash
python3 bot.py --strategy "..." --log-file ~/trades.log
```

Appends a JSON entry per run covering: strategy, portfolio before, signals, guardrail clips, plan, and execution summary.

### Read strategy from a file

```bash
echo "Buy high-momentum ETFs with strong 3-month performance, limit to 3 positions" > strategy.txt
python3 bot.py --strategy-file strategy.txt --dry-run
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
  min_rebalance_drift_pct: 0.03 # Skip rebalance if weight drifts < 3% from target
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
| `min_rebalance_drift_pct` | 3% | Skips rebalancing a position already near its target weight |
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

"Rebalance my portfolio to equal weight across current holdings."

"Trim any position that has drifted above 25% of my portfolio back to 20%."

"Research NVDA, AAPL, MSFT, AMZN and recommend the strongest buy given 
current valuations and recent news."

"Sell my weakest-performing position and redeploy into the strongest 
momentum name from this list: SPY, QQQ, IWM."
```

---

## Troubleshooting

**`Auth error: ROBINHOOD_MCP_TOKEN is not set`**
Run `python3 bot.py --test-connection`. If it fails, set `ROBINHOOD_MCP_TOKEN` manually or re-add the MCP server in Claude Code:
```bash
claude mcp add robinhood-trading --transport http https://agent.robinhood.com/mcp/trading
```

**`No candidate symbols to research`**
Add symbols via `--symbols` or set `static_symbols` in `config.yaml`.

**`Analysis failed: json.JSONDecodeError`**
Claude occasionally returns malformed JSON under heavy load. Re-run the command — the analyst prompt includes explicit JSON-only instructions and this is rare.

**Tavily not returning news**
The bot works without `TAVILY_API_KEY` — research falls back to fundamentals only. Set the key to enable news and analyst data.
