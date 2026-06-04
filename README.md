# Cozy Hare — Agentic Trading Bot

A Python CLI trading bot that researches stocks, generates trade signals from a natural language strategy, and executes orders through Robinhood.

---

## Architecture

The bot runs a structured three-phase pipeline. Python orchestrates each phase; Claude acts as a reasoning engine in phases 1–2 and as an agentic executor with live Robinhood tool access in phase 3. Safety guardrails are enforced in plain Python code between analysis and execution — Claude never bypasses them.

```
bot.py --strategy "..."
  │
  ├─ Phase 0: Portfolio Snapshot   Anthropic API → Robinhood MCP server
  │                                get_accounts → get_portfolio + get_equity_positions
  │                                Token auto-obtained via browser OAuth on first run,
  │                                then cached in macOS Keychain
  │
  ├─ Phase 1: Research             yfinance + Tavily web/news search
  │                                Fundamentals, price technicals, recent news,
  │                                analyst ratings for each candidate symbol
  │
  ├─ Phase 2: Analysis             Claude (no tools) → structured JSON
  │                                Reads research + current portfolio,
  │                                applies your strategy, returns TradeSignals
  │
  ├─ Guardrail Check               Pure Python — enforces position limits,
  │                                dollar caps, drift thresholds, sell/buy flags
  │
  ├─ Confirmation                  Rich terminal table + [y/N] prompt
  │                                (skippable with --no-confirm)
  │
  └─ Phase 3: Execution            Anthropic API → Robinhood MCP server
                                   Sells first, review_equity_order before every place,
                                   outputs structured execution summary
```

### How the Robinhood connection works

The bot uses the Anthropic SDK's MCP connector (`betas=["mcp-client-2025-11-20"]`). On first run it opens a browser-based OAuth flow (PKCE, no client secret) to authorize with Robinhood and caches the resulting token in macOS Keychain. Subsequent runs read the token from Keychain silently. If the token expires, run `python3 bot.py --reauth` to clear it and re-authorize.

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
│   └── executor.py         # Anthropic MCP loop — portfolio snapshot + trade execution
└── robinhood/
    ├── auth.py             # PKCE OAuth flow + macOS Keychain token cache
    └── models.py           # Pydantic models: PortfolioState, TradeSignal, RebalancePlan, etc.
```

---

## Setup

### 1. Prerequisites

- Python 3.9+
- A Robinhood account with an **Agentic** (cash) sub-account enabled
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

### 4. Test the connection

```bash
python3 bot.py --test-connection
```

On first run this opens a browser to authorize with Robinhood (PKCE OAuth). After you approve, the token is saved to macOS Keychain and reused on every subsequent run. No browser interaction needed after the first auth.

---

## Usage

### Basic run

```bash
python3 bot.py --strategy "Buy momentum tech stocks, max 5 positions, no single position > 20%"
```

The bot will:
1. Fetch your current portfolio via Robinhood MCP
2. Research candidate symbols
3. Generate trade signals via Claude
4. Show you a proposed trading plan
5. Ask for confirmation before placing any orders

### Dry run (no orders placed)

```bash
python3 bot.py --strategy "Trade into equal-weight mag7" --dry-run
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

**Token expired or invalid**
Run `python3 bot.py --reauth` to clear the cached Keychain token and re-run the browser OAuth flow.

**OAuth browser doesn't open**
The auth URL is printed to the terminal — copy and paste it into your browser manually to complete authorization.

**`No candidate symbols to research`**
Add symbols via `--symbols` or set `static_symbols` in `config.yaml`.

**`Analysis failed: json.JSONDecodeError`**
Claude occasionally returns malformed JSON under heavy load. Re-run the command — the analyst prompt includes explicit JSON-only instructions and this is rare.

**Tavily not returning news**
The bot works without `TAVILY_API_KEY` — research falls back to fundamentals only. Set the key to enable news and analyst data.
