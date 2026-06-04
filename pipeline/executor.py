from __future__ import annotations
import re
import threading
import httpx
import anthropic
from robinhood.models import PortfolioState, RebalancePlan, ExecutionSummary

ROBINHOOD_MCP_URL = "https://agent.robinhood.com/mcp/trading"
MCP_BETA = "mcp-client-2025-11-20"

READ_ONLY_TOOLS = [
    "get_accounts",
    "get_portfolio",
    "get_equity_positions",
]

EXECUTION_TOOLS = [
    "get_equity_quotes",
    "get_equity_tradability",
    "review_equity_order",
    "place_equity_order",
]


def _build_mcp_server(token: str) -> dict:
    return {
        "type": "url",
        "url": ROBINHOOD_MCP_URL,
        "name": "robinhood",
        "authorization_token": token,
    }


def _build_toolset(allowed_tools: list[str]) -> dict:
    return {
        "type": "mcp_toolset",
        "mcp_server_name": "robinhood",
        "default_config": {"enabled": False},
        "configs": {tool: {"enabled": True} for tool in allowed_tools},
    }


_CONNECT_TIMEOUT = httpx.Timeout(30.0, connect=30.0)
_WALL_CLOCK_TIMEOUT = 90  # seconds before a hung MCP tool call is abandoned


def _run_loop(
    client: anthropic.Anthropic,
    model: str,
    system: str,
    initial_message: str,
    token: str,
    allowed_tools: list[str],
    max_iterations: int = 20,
    on_status=None,
) -> list[dict]:
    mcp_server = _build_mcp_server(token)
    toolset = _build_toolset(allowed_tools)
    messages = [{"role": "user", "content": initial_message}]

    for i in range(max_iterations):
        if on_status:
            on_status(f"turn {i + 1}…")

        result: list = [None, None]  # [response, exception]

        def _stream_turn(turn=i):
            text_chars = 0
            try:
                with client.beta.messages.stream(
                    model=model,
                    max_tokens=4096,
                    system=system,
                    messages=messages,
                    mcp_servers=[mcp_server],
                    tools=[toolset],
                    betas=[MCP_BETA],
                    timeout=_CONNECT_TIMEOUT,
                ) as stream:
                    for event in stream:
                        if on_status:
                            etype = getattr(event, "type", None)
                            if etype == "content_block_start":
                                block = getattr(event, "content_block", None)
                                btype = getattr(block, "type", None)
                                if btype in ("tool_use", "mcp_tool_use"):
                                    name = getattr(block, "name", None) or getattr(block, "tool_name", "tool")
                                    on_status(f"turn {turn + 1} — {name}…")
                                    text_chars = 0
                                elif btype == "mcp_tool_result":
                                    on_status(f"turn {turn + 1} — tool result received…")
                                elif btype == "text":
                                    on_status(f"turn {turn + 1} — writing response…")
                                    text_chars = 0
                            elif etype == "content_block_delta":
                                delta = getattr(event, "delta", None)
                                if getattr(delta, "type", None) == "text_delta":
                                    text_chars += len(getattr(delta, "text", ""))
                                    on_status(f"turn {turn + 1} — writing response ({text_chars} chars)…")
                    result[0] = stream.get_final_message()
            except Exception as exc:
                result[1] = exc

        thread = threading.Thread(target=_stream_turn, daemon=True)
        thread.start()
        thread.join(timeout=_WALL_CLOCK_TIMEOUT)

        if thread.is_alive():
            raise RuntimeError(
                f"Executor timed out after {_WALL_CLOCK_TIMEOUT}s on turn {i + 1} "
                "(Robinhood MCP did not respond). Re-run or try --reauth."
            )
        if result[1] is not None:
            raise result[1]

        response = result[0]

        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "end_turn":
            return messages

        if response.stop_reason in ("pause_turn", "max_tokens"):
            messages.append({"role": "user", "content": "Please continue."})
            continue

        raise RuntimeError(f"Unexpected stop_reason: {response.stop_reason}")

    raise RuntimeError(f"Executor loop reached max_iterations={max_iterations} without finishing")


def _extract_json(text: str) -> str:
    text = text.strip()
    m = re.search(r'```(?:\w+)?\s*\n?([\s\S]*?)\n?```', text)
    if m:
        return m.group(1).strip()
    m = re.search(r'(\{[\s\S]*\})', text)
    if m:
        return m.group(1).strip()
    return text


def _extract_last_text(messages: list[dict]) -> str:
    for msg in reversed(messages):
        if msg.get("role") == "assistant":
            content = msg.get("content", [])
            if isinstance(content, list):
                for block in reversed(content):
                    if hasattr(block, "text"):
                        return block.text
                    if isinstance(block, dict) and block.get("type") == "text":
                        return block["text"]
            if isinstance(content, str):
                return content
    raise ValueError("No assistant text found in message history")


def get_portfolio_state(
    client: anthropic.Anthropic,
    model: str,
    token: str,
    on_status=None,
) -> PortfolioState:
    system = (
        "You are a portfolio data fetcher. "
        "Call get_accounts to find the agentic_allowed=true account, then call get_portfolio "
        "and get_equity_positions for that account. "
        "Return ONLY a single JSON object — no prose, no markdown — with keys: "
        "account_id (string), buying_power (number), total_value (number), "
        "positions (list of {symbol, quantity, market_value, avg_cost, unrealized_pnl_pct})."
    )
    messages = _run_loop(
        client, model, system,
        "Fetch my current portfolio state.",
        token, READ_ONLY_TOOLS,
        on_status=on_status,
    )
    raw = _extract_json(_extract_last_text(messages))
    return PortfolioState.model_validate_json(raw)


def execute_plan(
    client: anthropic.Anthropic,
    model: str,
    token: str,
    plan: RebalancePlan,
    account_id: str,
    max_iterations: int = 20,
    on_status=None,
) -> ExecutionSummary:
    system = f"""You are a trade execution agent for Robinhood account {account_id}.

EXECUTION RULES (strictly enforced):
1. Call review_equity_order BEFORE every place_equity_order. If review returns errors, skip that symbol.
2. Execute SELLS before BUYS.
3. Use type="market" and dollar_amount for all orders (fractional shares).
4. If get_equity_tradability fails for a symbol, skip it and record the reason.
5. When all orders are done (or skipped), output ONLY this JSON (no prose):
{{
  "completed": [{{"symbol": "...", "action": "buy"|"sell", "dollar_amount": 0.0, "order_id": "..."}}],
  "skipped": [{{"symbol": "...", "reason": "..."}}],
  "errors": [{{"symbol": "...", "error": "..."}}]
}}

REBALANCE PLAN TO EXECUTE:
{plan.model_dump_json(indent=2)}
"""
    messages = _run_loop(
        client, model, system,
        "Execute the rebalance plan.",
        token, EXECUTION_TOOLS,
        max_iterations=max_iterations,
        on_status=on_status,
    )
    raw = _extract_json(_extract_last_text(messages))
    return ExecutionSummary.model_validate_json(raw)
