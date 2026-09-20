"""backend/app/ingestion/vix_client.py
REST Client for AlpacaRelay GET /vix spot volatility prints and regime adaptation.
"""
from __future__ import annotations
import asyncio
from datetime import datetime, timezone
import logging
from typing import Any, Dict, Optional, Tuple
from zoneinfo import ZoneInfo
import httpx

from backend.app.config import settings
from backend.app.core.event_bus import EventBus, event_bus
from backend.app.models.events import RelayStatusEvent, VixPrint, VixRegime

log = logging.getLogger("VixClient")
ET_TZ = ZoneInfo("America/New_York")


class VixClient:
    """
    REST Client for querying spot VIX from AlpacaRelay dxFeed service.
    Enforces no-query-param rule, parses print payload, verifies staleness, and maps to volatility regimes.
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        relay_token: Optional[str] = None,
        poll_interval: Optional[float] = None,
        bus: Optional[EventBus] = None,
    ) -> None:
        self.base_url = (base_url or settings.RELAY_HTTP_URL).rstrip("/")
        self.relay_token = relay_token or settings.RELAY_TOKEN
        self.poll_interval = poll_interval or settings.VIX_POLL_INTERVAL_SEC
        self.bus: EventBus = bus or event_bus

        self.last_print: Optional[VixPrint] = None
        self._running: bool = False
        self._poller_task: Optional[asyncio.Task] = None
        self._http_client: Optional[httpx.AsyncClient] = None

    async def start(self) -> None:
        """Initialize HTTP client and start periodic polling task."""
        if self._running:
            return
        self._running = True
        self._http_client = httpx.AsyncClient(
            headers={"X-Relay-Token": self.relay_token},
            timeout=5.0
        )
        self._poller_task = asyncio.create_task(self._poll_loop(), name="VixPollerTask")
        log.info(f"VixClient polling started at {self.base_url}/vix every {self.poll_interval}s")

    async def stop(self) -> None:
        """Stop polling and close HTTP client."""
        self._running = False
        if self._poller_task:
            self._poller_task.cancel()
            try:
                await self._poller_task
            except asyncio.CancelledError:
                pass
            self._poller_task = None
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None
        log.info("VixClient stopped")

    async def fetch_vix(self) -> VixPrint:
        """Direct single query of GET /vix with error fallback."""
        if not self._http_client:
            self._http_client = httpx.AsyncClient(
                headers={"X-Relay-Token": self.relay_token},
                timeout=5.0
            )

        url = f"{self.base_url}/vix"  # Never append query parameters!
        try:
            resp = await self._http_client.get(url)
            if resp.status_code == 200:
                data = resp.json()
                return self._parse_vix_payload(data)
            elif resp.status_code == 503:
                log.warning("Upstream VIX unavailable (HTTP 503), using fallback cache")
                return self._get_fallback_print("unavailable")
            else:
                log.error(f"VIX endpoint returned HTTP {resp.status_code}: {resp.text}")
                return self._get_fallback_print(f"http_{resp.status_code}")
        except Exception as exc:
            log.error(f"Failed to query /vix: {exc}")
            return self._get_fallback_print("connection_error")

    def _parse_vix_payload(self, data: Dict[str, Any]) -> VixPrint:
        """Transform raw JSON payload into typed VixPrint and classify regime."""
        val = float(data.get("value", settings.VIX_DEFAULT_FALLBACK))
        asof_str = data.get("asof") or datetime.now(timezone.utc).isoformat()
        received_str = data.get("received_at") or datetime.now(timezone.utc).isoformat()
        age_s = float(data.get("age_s", 0.0))
        state = data.get("state", "ready")
        upstream = data.get("upstream", "connected")

        asof_dt = datetime.fromisoformat(str(asof_str).replace("Z", "+00:00"))
        if asof_dt.tzinfo is None:
            asof_dt = asof_dt.replace(tzinfo=timezone.utc)
        received_dt = datetime.fromisoformat(str(received_str).replace("Z", "+00:00"))
        if received_dt.tzinfo is None:
            received_dt = received_dt.replace(tzinfo=timezone.utc)

        regime, multiplier = self.classify_regime(val)

        # Freshness check during regular market hours
        is_stale = (age_s > settings.VIX_MAX_STALE_AGE_SEC) if self._is_market_hours() else False

        vix_print = VixPrint(
            value=val,
            asof=asof_dt,
            received_at=received_dt,
            age_s=age_s,
            state=state,
            upstream=upstream,
            regime=regime,
            sizing_multiplier=multiplier,
            is_stale=is_stale,
            is_fallback=False,
        )
        self.last_print = vix_print
        return vix_print

    @staticmethod
    def classify_regime(vix: float) -> Tuple[VixRegime, float]:
        """Map raw VIX value to institutional volatility regime and sizing multiplier."""
        if vix < 15.0:
            return VixRegime.LOW, 1.20
        elif vix < 22.0:
            return VixRegime.NORMAL, 1.00
        elif vix < 30.0:
            return VixRegime.ELEVATED, 0.60
        else:
            return VixRegime.CRISIS, 0.25

    def _get_fallback_print(self, reason: str) -> VixPrint:
        """Generate safe fallback when live /vix endpoint fails."""
        if self.last_print:
            # Return cached print with is_stale set
            return VixPrint(
                value=self.last_print.value,
                asof=self.last_print.asof,
                received_at=datetime.now(timezone.utc),
                age_s=self.last_print.age_s,
                state="stale",
                upstream="down",
                regime=self.last_print.regime,
                sizing_multiplier=self.last_print.sizing_multiplier,
                is_stale=True,
                is_fallback=False,
            )

        now = datetime.now(timezone.utc)
        regime, multiplier = self.classify_regime(settings.VIX_DEFAULT_FALLBACK)
        return VixPrint(
            value=settings.VIX_DEFAULT_FALLBACK,
            asof=now,
            received_at=now,
            age_s=0.0,
            state="fallback",
            upstream="down",
            regime=regime,
            sizing_multiplier=multiplier,
            is_stale=True,
            is_fallback=True,
        )

    def _is_market_hours(self) -> bool:
        """Determine if current time is US regular trading hours (09:30-16:00 ET Mon-Fri)."""
        now_et = datetime.now(ET_TZ)
        if now_et.weekday() > 4:  # Sat=5, Sun=6
            return False
        minutes_et = now_et.hour * 60 + now_et.minute
        # 09:30 is 570, 16:00 is 960
        return 570 <= minutes_et <= 960

    async def _poll_loop(self) -> None:
        """Continuous background polling loop."""
        while self._running:
            try:
                vprint = await self.fetch_vix()
                await self.bus.publish(
                    RelayStatusEvent(
                        feed_type="vix",
                        status="connected" if not vprint.is_fallback and vprint.upstream == "connected" else "degraded",
                        message="VIX print available" if not vprint.is_fallback else "VIX fallback in use",
                    )
                )
                await self.bus.publish(vprint)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.error(f"VIX polling loop error: {exc}")

            try:
                await asyncio.sleep(self.poll_interval)
            except asyncio.CancelledError:
                break
