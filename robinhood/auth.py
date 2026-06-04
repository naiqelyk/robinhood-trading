from __future__ import annotations
import json
import os
from pathlib import Path


def get_robinhood_mcp_token() -> str:
    """
    Resolves the Robinhood MCP Bearer token.
    Checks ROBINHOOD_MCP_TOKEN env var first, then ~/.claude.json.
    """
    token = os.environ.get("ROBINHOOD_MCP_TOKEN")
    if token:
        return token

    claude_json = Path.home() / ".claude.json"
    if not claude_json.exists():
        raise FileNotFoundError(
            "ROBINHOOD_MCP_TOKEN is not set and ~/.claude.json was not found.\n"
            "Set the env var or ensure Claude Code is configured:\n"
            "  claude mcp add robinhood-trading --transport http https://agent.robinhood.com/mcp/trading"
        )

    with open(claude_json) as f:
        data = json.load(f)

    mcp_servers = data.get("mcpServers", {})
    entry = mcp_servers.get("robinhood-trading") or mcp_servers.get("robinhood")

    if not entry:
        raise KeyError(
            "Robinhood MCP server not found in ~/.claude.json.\n"
            "Run: claude mcp add robinhood-trading --transport http https://agent.robinhood.com/mcp/trading"
        )

    token = entry.get("authorizationToken") or entry.get("authorization_token")
    if not token:
        raise ValueError(
            "Robinhood MCP entry found in ~/.claude.json but no token is stored.\n"
            "Set ROBINHOOD_MCP_TOKEN manually or re-authenticate via Claude Code."
        )

    return token
