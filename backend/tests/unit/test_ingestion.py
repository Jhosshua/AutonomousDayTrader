"""backend/tests/unit/test_ingestion.py
Unit test suite for FinancialSentimentScorer, VixClient, Stock/News models, and EventBus fault isolation.
"""
import asyncio
from datetime import datetime, timezone
import time
import pytest
import httpx

from backend.app.core.event_bus import EventBus
from backend.app.ingestion.sentiment import FinancialSentimentScorer
from backend.app.ingestion.vix_client import VixClient
from backend.app.models.events import (
    BarEvent,
    QuoteEvent,
    TradeEvent,
    NewsEvent,
    VixPrint,
    VixRegime,
    CatalystCategory,
)


# --- 1. Financial Sentiment Scorer Tests ---

def test_sentiment_bullish_catalysts():
    scorer = FinancialSentimentScorer()

    score, conf, cat = scorer.score("AAPL beats estimates with record earnings for Q3")
    assert score >= 0.60
    assert conf >= 0.50
    assert cat == CatalystCategory.EARNINGS_BEAT

    score_fda, _, cat_fda = scorer.score("Biotech announces FDA approval for breakthrough therapy")
    assert score_fda >= 0.60
    assert cat_fda == CatalystCategory.FDA_APPROVAL


def test_sentiment_bearish_catalysts():
    scorer = FinancialSentimentScorer()

    score, conf, cat = scorer.score("SEC investigation opened into company following fraud allegations")
    assert score <= -0.60
    assert cat == CatalystCategory.LEGAL_INVESTIGATION

    score_miss, _, cat_miss = scorer.score("Company misses estimates and slashes forecast for fiscal year")
    assert score_miss <= -0.60
    assert cat_miss in (CatalystCategory.EARNINGS_MISS, CatalystCategory.GUIDANCE_CUT)


def test_sentiment_negation_inversion():
    scorer = FinancialSentimentScorer()

    pos_score, _, _ = scorer.score("Company succeeds in beating revenue targets")
    neg_score, _, _ = scorer.score("Company fails to beat revenue targets")

    assert pos_score > 0.0
    assert neg_score < 0.0


def test_sentiment_intensifier_scaling():
    scorer = FinancialSentimentScorer()

    base_score, _, _ = scorer.score("Company beats estimates")
    intensified_score, _, _ = scorer.score("Company massively beats estimates with record revenue")

    assert intensified_score > base_score


def test_sentiment_sub_millisecond_benchmark():
    scorer = FinancialSentimentScorer()
    headline = "Apple beats Q3 estimates with record iPhone revenue and raised guidance"

    start = time.perf_counter()
    n_iterations = 1000
    for _ in range(n_iterations):
        scorer.score(headline)
    elapsed = time.perf_counter() - start

    avg_ms = (elapsed / n_iterations) * 1000.0
    # Average latency should be well under 0.1 ms (100 microseconds)
    assert avg_ms < 0.5, f"Scoring too slow: {avg_ms:.4f} ms per headline"


# --- 2. VIX Client & Regime Classification Tests ---

def test_vix_regime_classification_boundaries():
    assert VixClient.classify_regime(14.99) == (VixRegime.LOW, 1.20)
    assert VixClient.classify_regime(15.00) == (VixRegime.NORMAL, 1.00)
    assert VixClient.classify_regime(24.99) == (VixRegime.NORMAL, 1.00)
    assert VixClient.classify_regime(25.00) == (VixRegime.ELEVATED, 0.70)
    assert VixClient.classify_regime(34.99) == (VixRegime.ELEVATED, 0.70)
    assert VixClient.classify_regime(35.00) == (VixRegime.CRISIS, 0.35)
    assert VixClient.classify_regime(45.00) == (VixRegime.CRISIS, 0.35)


@pytest.mark.asyncio
async def test_vix_fetch_successful_parse():
    client = VixClient(base_url="http://127.0.0.1:9999", relay_token="dummy")

    mock_payload = {
        "value": 17.50,
        "asof": "2026-09-21T09:30:00Z",
        "received_at": "2026-09-21T09:30:01Z",
        "age_s": 1.2,
        "state": "ready",
        "upstream": "connected",
    }

    # Mock internal httpx client response
    class MockResponse:
        status_code = 200
        def json(self):
            return mock_payload

    class MockAsyncClient:
        async def get(self, url):
            assert "?" not in url, "VIX query must strictly contain no query parameters"
            return MockResponse()

    client._http_client = MockAsyncClient()
    vprint = await client.fetch_vix()

    assert vprint.value == 17.50
    assert vprint.regime == VixRegime.NORMAL
    assert vprint.sizing_multiplier == 1.00
    assert vprint.state == "ready"
    assert vprint.is_fallback is False


@pytest.mark.asyncio
async def test_vix_503_fallback_cache():
    client = VixClient(base_url="http://127.0.0.1:9999", relay_token="dummy")

    class Mock503Response:
        status_code = 503
        text = "Service Unavailable"

    class MockAsyncClient:
        async def get(self, url):
            return Mock503Response()

    client._http_client = MockAsyncClient()
    vprint = await client.fetch_vix()

    assert vprint.is_fallback is True
    assert vprint.value == 20.0
    assert vprint.regime == VixRegime.NORMAL
    assert vprint.is_stale is True


# --- 3. Event Models & Deserialization Tests ---

def test_bar_event_from_relay_dict():
    raw_bar = {
        "T": "b",
        "S": "AAPL",
        "o": 150.25,
        "h": 151.10,
        "l": 149.80,
        "c": 150.90,
        "v": 12500,
        "t": "2026-09-21T09:31:00Z",
        "n": 420,
        "vw": 150.60,
    }
    bar = BarEvent.from_relay_dict(raw_bar)
    assert bar.symbol == "AAPL"
    assert bar.open == 150.25
    assert bar.high == 151.10
    assert bar.low == 149.80
    assert bar.close == 150.90
    assert bar.volume == 12500
    assert bar.num_trades == 420
    assert bar.vwap == 150.60


def test_quote_event_from_relay_dict():
    raw_quote = {
        "T": "q",
        "S": "TSLA",
        "bp": 199.95,
        "bs": 10,
        "bx": "V",
        "ap": 200.05,
        "as": 15,
        "ax": "V",
        "t": "2026-09-21T09:30:05.123456Z",
        "c": ["R"],
        "z": "C",
    }
    q = QuoteEvent.from_relay_dict(raw_quote)
    assert q.symbol == "TSLA"
    assert q.bid_price == 199.95
    assert q.ask_price == 200.05
    assert q.mid_price == 200.00
    assert q.spread == 0.10


def test_trade_event_from_relay_dict():
    raw_trade = {
        "T": "t",
        "S": "NVDA",
        "i": 987654,
        "p": 121.50,
        "s": 100,
        "x": "V",
        "t": "2026-09-21T09:30:10Z",
        "c": ["@"],
        "z": "C",
    }
    t = TradeEvent.from_relay_dict(raw_trade)
    assert t.symbol == "NVDA"
    assert t.trade_id == 987654
    assert t.price == 121.50
    assert t.size == 100


# --- 4. Event Bus Fault Isolation Tests ---

@pytest.mark.asyncio
async def test_event_bus_subscriber_exception_isolation():
    bus = EventBus()
    received_events = []

    async def faulty_subscriber(bar: BarEvent):
        raise ZeroDivisionError("Simulated catastrophic subscriber failure")

    async def healthy_subscriber(bar: BarEvent):
        received_events.append(bar)

    bus.subscribe(BarEvent, faulty_subscriber)
    bus.subscribe(BarEvent, healthy_subscriber)

    sample_bar = BarEvent(
        symbol="SPY",
        open=500.0,
        high=501.0,
        low=499.0,
        close=500.5,
        volume=1000,
        timestamp=datetime.now(timezone.utc),
    )

    # Publishing must NOT raise an exception
    await bus.publish(sample_bar)

    # Healthy subscriber should have received the event regardless of faulty subscriber failure
    assert len(received_events) == 1
    assert received_events[0].symbol == "SPY"
    assert bus.metrics["error_count"] == 1
    assert bus.metrics["published_count"] == 1


# --- 5. Live WebSocket Ingestion & Handshake Tests ---

@pytest.mark.asyncio
async def test_stock_ws_live_handshake_and_dispatch():
    from backend.app.replay.mock_relay import MockAlpacaRelayServer
    from backend.app.ingestion.stock_ws import StockWebSocketClient

    test_port = 8991
    test_token = "test_secret_token_123"
    server = MockAlpacaRelayServer(host="127.0.0.1", port=test_port, relay_token=test_token)
    await server.start()

    bus = EventBus()
    received_bars = []
    received_quotes = []

    async def on_bar(bar: BarEvent):
        received_bars.append(bar)

    async def on_quote(quote: QuoteEvent):
        received_quotes.append(quote)

    bus.subscribe(BarEvent, on_bar)
    bus.subscribe(QuoteEvent, on_quote)

    client = StockWebSocketClient(
        relay_url=f"ws://127.0.0.1:{test_port}/v2/stocks",
        relay_token=test_token,
        symbols=["AAPL", "TSLA"],
        bus=bus,
    )

    try:
        await client.start()
        # Wait for client to connect and authenticate
        for _ in range(30):
            if client.is_connected:
                break
            await asyncio.sleep(0.1)

        assert client.is_connected is True

        # Broadcast bar and quote from mock relay
        await server.broadcast_bar({
            "T": "b",
            "S": "AAPL",
            "o": 150.0,
            "h": 151.0,
            "l": 149.5,
            "c": 150.5,
            "v": 5000,
            "t": "2026-09-21T09:35:00Z",
        })
        await server.broadcast_quote({
            "T": "q",
            "S": "TSLA",
            "bp": 199.5,
            "bs": 10,
            "ap": 199.7,
            "as": 20,
            "t": "2026-09-21T09:35:01Z",
        })

        # Allow queue worker to process
        for _ in range(30):
            if len(received_bars) > 0 and len(received_quotes) > 0:
                break
            await asyncio.sleep(0.1)

        assert len(received_bars) == 1
        assert received_bars[0].symbol == "AAPL"
        assert received_bars[0].close == 150.5

        assert len(received_quotes) == 1
        assert received_quotes[0].symbol == "TSLA"
        assert received_quotes[0].bid_price == 199.5
    finally:
        await client.stop()
        await server.stop()


@pytest.mark.asyncio
async def test_news_ws_live_handshake_and_sentiment_enrichment():
    from backend.app.replay.mock_relay import MockAlpacaRelayServer
    from backend.app.ingestion.news_ws import NewsWebSocketClient

    test_port = 8992
    test_token = "test_news_token_456"
    server = MockAlpacaRelayServer(host="127.0.0.1", port=test_port, relay_token=test_token)
    await server.start()

    bus = EventBus()
    received_news = []

    async def on_news(news: NewsEvent):
        received_news.append(news)

    bus.subscribe(NewsEvent, on_news)

    client = NewsWebSocketClient(
        relay_url=f"ws://127.0.0.1:{test_port}/news",
        relay_token=test_token,
        bus=bus,
    )

    try:
        await client.start()
        for _ in range(30):
            if client.is_connected:
                break
            await asyncio.sleep(0.1)

        assert client.is_connected is True

        # Broadcast news article from mock relay
        await server.broadcast_news({
            "T": "n",
            "id": 9991,
            "headline": "AAPL beats estimates with record blowout quarter revenue",
            "summary": "Apple reports record profit surging past analyst consensus.",
            "symbols": ["AAPL"],
            "source": "benzinga",
            "created_at": "2026-09-21T09:36:00Z",
        })

        for _ in range(30):
            if len(received_news) > 0:
                break
            await asyncio.sleep(0.1)

        assert len(received_news) == 1
        ev = received_news[0]
        assert ev.article_id == 9991
        assert "AAPL" in ev.symbols
        assert ev.sentiment_score >= 0.60
        assert ev.catalyst_category == CatalystCategory.EARNINGS_BEAT
        assert ev.is_actionable_bullish is True
    finally:
        await client.stop()
        await server.stop()

