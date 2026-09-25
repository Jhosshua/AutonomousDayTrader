"""backend/app/config.py
System configuration, network endpoints, credentials, and institutional risk parameters.
"""
from typing import List, Optional
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    # Environment & Mode
    ENV: str = Field(default="development", description="development, production, or testing")
    LOG_LEVEL: str = Field(default="INFO", description="Logging verbosity")

    # AlpacaRelay Endpoints & Credentials
    RELAY_URL: str = Field(
        default="wss://alpacarelay-production.up.railway.app",
        description="AlpacaRelay WebSocket base endpoint (or ws://127.0.0.1:8080 for mock)"
    )
    RELAY_HTTP_URL: str = Field(
        default="https://alpacarelay-production.up.railway.app",
        description="AlpacaRelay HTTP base endpoint (or http://127.0.0.1:8080 for mock)"
    )
    RELAY_TOKEN: str = Field(
        default="",
        description="Shared authentication token for AlpacaRelay; supplied only through the environment"
    )
    START_RELAY_CLIENTS: bool = Field(
        default=True,
        description="Start AlpacaRelay stock, news, and VIX clients during application lifespan"
    )

    # Order execution. "simulated" = built-in fill simulator (tests, replay,
    # local dev). "alpaca_paper" = every fill is a real order on the Alpaca
    # paper account; market data still comes from AlpacaRelay.
    BROKER_MODE: str = Field(default="simulated", description="simulated or alpaca_paper")
    ALPACA_API_KEY: str = Field(default="", description="Alpaca paper key id; environment only")
    ALPACA_SECRET_KEY: str = Field(default="", description="Alpaca paper secret; environment only")
    ALPACA_BASE_URL: str = Field(default="https://paper-api.alpaca.markets", description="Paper trading API only")
    BROKER_RECONCILE_SEC: float = Field(default=30.0, description="How often local positions are compared with Alpaca")

    # Durable singleton state. Production enables this against a Railway volume;
    # tests and local development remain opt-in so replays cannot contaminate the
    # live paper ledger.
    PERSISTENCE_ENABLED: bool = Field(
        default=False,
        description="Persist account, orders, brackets, strategies, and history"
    )
    PERSISTENCE_REQUIRED: bool = Field(
        default=False,
        description="Fail startup instead of silently creating a fresh account when no checkpoint exists"
    )
    STATE_DB_PATH: str = Field(
        default=".data/trading_state.sqlite3",
        description="SQLite ledger path; production must point this at a durable mounted volume"
    )
    RESEARCH_ENABLED: bool = Field(
        default=True,
        description="Record research rows (signals, trade details) for later backtesting; observation only"
    )
    RESEARCH_DB_PATH: str = Field(
        default="",
        description="Research SQLite path. Empty = research.sqlite3 next to STATE_DB_PATH when persistence is on, else memory only"
    )
    STATE_BACKUP_PATH: str = Field(
        default="",
        description="Optional same-volume hot backup created at session boundaries and shutdown"
    )

    # Active Ticker Universes & Subscriptions
    WATCHLIST_SYMBOLS: List[str] = Field(
        default=["SPY", "QQQ", "AAPL", "NVDA", "TSLA", "AMD", "MSFT", "AMZN", "META", "GOOGL", "PLTR", "COIN"],
        description="Default symbol roster for stock market data subscriptions"
    )
    SWING_SYMBOLS: List[str] = Field(
        default=["LRCX", "KLAC", "MU", "AMD", "GS"],
        description="Certified 5 stocks for 2-Day Panic Dip swing strategy"
    )
    SWING_BENCHMARK: str = Field(
        default="QQQ",
        description="Benchmark index for 60-day relative strength filter"
    )
    SWING_SLOT_NOTIONAL: float = Field(
        default=25000.0,
        description="Target notional allocation per swing trade slot"
    )
    SWING_MAX_CONCURRENT_POSITIONS: int = Field(
        default=2,
        description="Maximum concurrent open swing positions"
    )
    DAILY_BARS_SEED_PATH: str = Field(
        default="backend/app/data/daily_bars_seed.json",
        description="Path to historical daily bars seed data"
    )
    EARNINGS_CALENDAR_SEED_PATH: str = Field(
        default="backend/app/data/earnings_calendar.json",
        description="Path to earnings calendar seed data"
    )
    EARNINGS_CALENDAR_REMOTE_URL: Optional[str] = Field(
        default=None,
        description="Optional remote URL to refresh earnings calendar"
    )
    EARNINGS_CALENDAR_CACHE_PATH: str = Field(
        default="backend/app/data/earnings_calendar_cache.json",
        description="Path to durable earnings calendar cache file"
    )
    SUBSCRIBE_BARS: bool = Field(default=True, description="Subscribe to 1-minute OHLCV bars")
    SUBSCRIBE_QUOTES: bool = Field(default=True, description="Subscribe to NBBO top-of-book quotes")
    SUBSCRIBE_TRADES: bool = Field(default=True, description="Subscribe to SIP trade prints")
    SUBSCRIBE_NEWS: bool = Field(default=True, description="Subscribe to Benzinga news feed")

    # Stock & News WebSocket Connection Tuning
    WS_CONNECT_TIMEOUT_SEC: float = Field(default=10.0, description="Connection handshake timeout")
    WS_AUTH_TIMEOUT_SEC: float = Field(default=10.0, description="Auth ack timeout (relay enforced)")
    WS_PING_INTERVAL_SEC: float = Field(default=20.0, description="Keepalive ping interval")
    WS_PING_TIMEOUT_SEC: float = Field(default=10.0, description="Keepalive ping timeout")
    WS_RECONNECT_INITIAL_BACKOFF_SEC: float = Field(default=1.0, description="Initial reconnect delay")
    WS_RECONNECT_MAX_BACKOFF_SEC: float = Field(default=30.0, description="Max exponential backoff")
    WS_RECONNECT_BACKOFF_MULTIPLIER: float = Field(default=2.0, description="Backoff multiplier")
    WS_MAX_MESSAGE_SIZE_BYTES: int = Field(default=8 * 1024 * 1024, description="Max frame size (8MB)")

    # Ingestion Queue & Backpressure Handling
    QUEUE_MAX_SIZE: int = Field(
        default=10000,
        description="Max internal queue depth before backpressure actions are triggered"
    )
    QUEUE_HIGH_WATERMARK_PCT: float = Field(
        default=0.80,
        description="Queue threshold (80%) triggering high-watermark warning and load shed"
    )

    # REST /vix Client Settings
    VIX_POLL_INTERVAL_SEC: float = Field(default=5.0, description="Polling interval in seconds")
    VIX_MAX_STALE_AGE_SEC: float = Field(default=300.0, description="Max allowed staleness during market hours")
    VIX_DEFAULT_FALLBACK: float = Field(default=20.0, description="Safe fallback VIX print on failure")

    # Institutional Account & Risk Parameters
    INITIAL_CASH: float = Field(default=50000.0, description="Initial virtual account cash balance")
    DAY_TRADING_LEVERAGE: float = Field(default=4.0, description="FINRA Rule 4210 Day Trading Buying Power (4:1)")
    MAX_DAILY_LOSS_LIMIT: float = Field(default=1500.0, description="Hard daily drawdown circuit breaker ($1,500)")
    PER_POSITION_RISK_PCT: float = Field(default=0.01, description="Max account equity risk per trade (1%)")
    MAX_POSITION_NOTIONAL: float = Field(default=25000.0, description="Max single position value ($25,000 = 50% of equity, 12.5% of 4:1 DTBP)")
    MAX_CONCURRENT_POSITIONS: int = Field(default=3, description="Max simultaneous open positions")

    # Safe Host Port Allocations (Collision Free)
    API_PORT: int = Field(default=8005, description="FastAPI core engine & WS port")
    UI_PORT: int = Field(default=3005, description="Mobile trading UI frontend port")
    MOCK_PORT: int = Field(default=8080, description="Mock AlpacaRelay replay server port")


# Global singleton settings instance
settings = Settings()
