from __future__ import annotations
import os
from dataclasses import dataclass, field, asdict
import json
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
