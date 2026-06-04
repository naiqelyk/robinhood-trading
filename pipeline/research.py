from __future__ import annotations
import os
import re
import json
from collections import Counter
from dataclasses import dataclass, field, asdict
import anthropic
import yfinance as yf
from tavily import TavilyClient


@dataclass
class FundamentalData:
    symbol: str
    current_price: float
    pe_ratio: float | None
    forward_pe: float | None
    market_cap: float | None
    revenue_growth: float | None
    earnings_growth: float | None
    beta: float | None
    week_52_high: float | None
    week_52_low: float | None
    ma_50: float | None
    ma_200: float | None
    momentum_63d: float | None
    above_50d_ma: bool
    above_200d_ma: bool
    golden_cross: bool


@dataclass
class NewsData:
    symbol: str
    articles: list[str] = field(default_factory=list)
    analyst_snippets: list[str] = field(default_factory=list)


@dataclass
class SymbolResearch:
    fundamentals: FundamentalData
    news: NewsData


class ResearchBundle:
    def __init__(self) -> None:
        self.data: dict[str, SymbolResearch] = {}

    def add(self, symbol: str, research: SymbolResearch) -> None:
        self.data[symbol] = research

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(
            {sym: asdict(r) for sym, r in self.data.items()},
            indent=indent,
            default=str,
        )


def fetch_fundamentals(symbol: str) -> FundamentalData:
    ticker = yf.Ticker(symbol)
    info = ticker.info or {}

    try:
        hist = yf.download(symbol, period="1y", progress=False, auto_adjust=True)
        if hist.empty:
            raise ValueError("No price history")
        close = hist["Close"].squeeze()
        current_price = float(close.iloc[-1])
        ma_50 = float(close.rolling(50).mean().iloc[-1]) if len(close) >= 50 else None
        ma_200 = float(close.rolling(200).mean().iloc[-1]) if len(close) >= 200 else None
        momentum_63d = float(close.iloc[-1] / close.iloc[-63] - 1) if len(close) >= 63 else None
    except Exception:
        current_price = float(info.get("currentPrice") or info.get("regularMarketPrice") or 0)
        ma_50 = ma_200 = momentum_63d = None

    above_50 = (ma_50 is not None and current_price > ma_50)
    above_200 = (ma_200 is not None and current_price > ma_200)
    golden = (ma_50 is not None and ma_200 is not None and ma_50 > ma_200)

    return FundamentalData(
        symbol=symbol,
        current_price=current_price,
        pe_ratio=info.get("trailingPE"),
        forward_pe=info.get("forwardPE"),
        market_cap=info.get("marketCap"),
        revenue_growth=info.get("revenueGrowth"),
        earnings_growth=info.get("earningsGrowth"),
        beta=info.get("beta"),
        week_52_high=info.get("fiftyTwoWeekHigh"),
        week_52_low=info.get("fiftyTwoWeekLow"),
        ma_50=ma_50,
        ma_200=ma_200,
        momentum_63d=momentum_63d,
        above_50d_ma=above_50,
        above_200d_ma=above_200,
        golden_cross=golden,
    )


def fetch_news(symbol: str, max_articles: int = 5) -> NewsData:
    api_key = os.environ.get("TAVILY_API_KEY")
    if not api_key:
        return NewsData(symbol=symbol)

    tavily = TavilyClient(api_key=api_key)
    articles: list[str] = []
    analyst_snippets: list[str] = []

    try:
        news_resp = tavily.search(
            query=f"{symbol} stock news earnings",
            topic="news",
            search_depth="advanced",
            max_results=max_articles,
            days=7,
        )
        articles = [r.get("content", "") for r in news_resp.get("results", [])]
    except Exception:
        pass

    try:
        finance_resp = tavily.search(
            query=f"{symbol} analyst rating price target outlook",
            topic="finance",
            search_depth="basic",
            max_results=3,
        )
        analyst_snippets = [r.get("content", "") for r in finance_resp.get("results", [])]
    except Exception:
        pass

    return NewsData(symbol=symbol, articles=articles, analyst_snippets=analyst_snippets)


# Common English words and finance abbreviations that aren't stock tickers
_TICKER_BLOCKLIST = {
    "A", "I", "AM", "AN", "AT", "BE", "BY", "DO", "GO", "HE", "IF", "IN",
    "IS", "IT", "ME", "MY", "NO", "OF", "ON", "OR", "SO", "TO", "UP", "US",
    "WE", "AND", "ARE", "BUT", "FOR", "HAS", "HOW", "ITS", "LET", "NOT",
    "NOW", "OFF", "OUR", "OUT", "OWN", "SAY", "SHE", "THE", "TOO", "TWO",
    "USE", "WAS", "WAY", "WHO", "WHY", "YET", "YOU", "ALSO", "BACK", "BEEN",
    "COME", "DOES", "EACH", "EVEN", "FROM", "GIVE", "HAVE", "HERE", "JUST",
    "KNOW", "LIKE", "LOOK", "MAKE", "MOST", "MUCH", "NEED", "ONLY", "OVER",
    "SAID", "SAME", "SEEM", "SOME", "SUCH", "THAN", "THAT", "THEM", "THEN",
    "THEY", "THIS", "TIME", "VERY", "WANT", "WELL", "WERE", "WHAT", "WHEN",
    "WITH", "YEAR", "YOUR", "ABOUT", "AFTER", "AGAIN", "COULD", "EVERY",
    "FIRST", "FOUND", "GOING", "GREAT", "MIGHT", "OTHER", "RIGHT", "SINCE",
    "THINK", "THREE", "UNDER", "UNTIL", "WHERE", "WHICH", "WHILE", "WOULD",
    # Finance/Reddit jargon that isn't a ticker
    "DD", "ER", "PM", "AH", "IMO", "IMHO", "TBH", "FYI", "EOD", "EOM",
    "ETF", "IPO", "YTD", "ATH", "ALL", "NEW", "BIG", "HIGH", "LOW", "TOP",
    "CEO", "CFO", "CTO", "COO", "SEC", "NYSE", "GDP", "FED", "USA", "UK",
    "USD", "EUR", "GBP", "API", "YOLO", "FOMO", "HODL", "REIT", "SPAC",
    "BUY", "SELL", "HOLD", "LONG", "SHORT", "PUTS", "CALL", "CALLS",
    "STOCK", "TRADE", "PRICE", "GAIN", "LOSS", "BULL", "BEAR", "MOON",
    "GOOD", "BEST", "NEXT", "LAST", "WEEK", "DAYS", "CASH", "DEBT", "FUND",
    "NEWS", "EDIT", "LINK", "POST", "SITE", "TEXT", "VIEW", "DONE", "OPEN",
    "RATE", "RISK", "PLAN", "DATA", "YEAR", "SAID", "MUST", "ABLE", "BOTH",
    "DOWN", "LESS", "MORE", "MUCH", "MANY", "MOST", "NEAR", "ONLY", "PAST",
    "PLUS", "REAL", "SAME", "SEEN", "SURE", "TAKE", "THAN", "THEN", "THEY",
    "TRUE", "TURN", "TYPE", "USED", "ZERO", "BEEN", "EACH", "EVEN", "EVER",
    "FREE", "FROM", "FULL", "GAVE", "GETS", "GIVE", "GOES", "GONE", "GREW",
    "GROW", "HELP", "HOME", "HOPE", "HUGE", "INTO", "JOIN", "JUST", "KEEP",
    "KNEW", "KNOW", "LATE", "LEAD", "LEFT", "LIFE", "LINE", "LIVE", "LOSS",
    "LOST", "LOVE", "MAIN", "MAKE", "MEAN", "MEET", "MOVE", "NEED", "ONCE",
    "ONES", "ONLY", "OPEN", "PART", "PLAY", "PUTS", "READ", "RISE", "ROAD",
    "ROLE", "RULE", "RUNS", "SELL", "SENT", "SETS", "SHOW", "SIDE", "SIGN",
    "SOLD", "SOME", "SOON", "SORT", "STAY", "STEP", "STOP", "TALK", "TELL",
    "TERM", "TEST", "TILL", "TOLD", "TOOK", "TOPS", "TOWN", "UNIT", "UPON",
    "WAIT", "WALL", "WENT", "WIDE", "WILL", "WINS", "WORD", "WORK", "ZONE",
}


def _build_discovery_queries(client: anthropic.Anthropic, strategy: str) -> list[str]:
    """Use Claude to turn the strategy into targeted search queries."""
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=512,
        messages=[{
            "role": "user",
            "content": (
                f"Investment strategy: {strategy}\n\n"
                "Generate 5 web search queries to find specific stock tickers that fit this strategy. "
                "Mix sources: Reddit (r/wallstreetbets, r/stocks, r/investing), financial news, and analyst sites. "
                "Make queries specific to the strategy's themes (sector, style, criteria). "
                "Return ONLY a JSON array of 5 strings, no other text."
            ),
        }],
    )
    raw = response.content[0].text.strip()
    m = re.search(r'(\[[\s\S]*\])', raw)
    if m:
        raw = m.group(1)
    return json.loads(raw)


def discover_symbols(
    strategy: str = "",
    max_symbols: int = 10,
    client: anthropic.Anthropic | None = None,
    console=None,
) -> list[str]:
    """Search Reddit and financial news for stock symbols matching the strategy."""
    api_key = os.environ.get("TAVILY_API_KEY")
    if not api_key:
        return []

    tavily = TavilyClient(api_key=api_key)

    # Use Claude to generate strategy-specific queries when a strategy and client are provided
    if strategy and client:
        try:
            queries = _build_discovery_queries(client, strategy)
        except Exception:
            queries = []
    else:
        queries = []

    # Always include a couple of broad Reddit queries as a fallback/supplement
    fallback = [
        "reddit r/stocks r/investing top stock picks this week",
        "trending stocks analysts recommend buy now",
    ]
    queries = queries + [q for q in fallback if q not in queries]

    all_text = ""
    for query in queries:
        try:
            resp = tavily.search(
                query=query,
                search_depth="basic",
                max_results=5,
                days=7,
            )
            for r in resp.get("results", []):
                all_text += " " + r.get("content", "") + " " + r.get("title", "")
        except Exception:
            pass

    if not all_text.strip():
        return []

    candidates = re.findall(r'\b([A-Z]{1,5})\b', all_text)
    counts = Counter(c for c in candidates if c not in _TICKER_BLOCKLIST and len(c) >= 2)

    top = [sym for sym, _ in counts.most_common(max_symbols * 3)]
    return top[:max_symbols]


def research_symbols(
    symbols: list[str],
    max_articles: int = 5,
    console=None,
) -> ResearchBundle:
    bundle = ResearchBundle()
    for symbol in symbols:
        try:
            if console:
                with console.status(f"  [cyan]{symbol}[/cyan] — fetching fundamentals…"):
                    fundamentals = fetch_fundamentals(symbol)
                with console.status(f"  [cyan]{symbol}[/cyan] — fetching news…"):
                    news = fetch_news(symbol, max_articles)
                console.print(f"  [green]✓[/green] [cyan]{symbol}[/cyan]")
            else:
                fundamentals = fetch_fundamentals(symbol)
                news = fetch_news(symbol, max_articles)
            bundle.add(symbol, SymbolResearch(fundamentals=fundamentals, news=news))
        except Exception as e:
            if console:
                console.print(f"  [yellow]Warning: could not fetch data for {symbol}: {e}[/yellow]")
    return bundle
