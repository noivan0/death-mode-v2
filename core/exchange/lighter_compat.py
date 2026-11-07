"""Lighter API compatibility helpers — 2026 Q4 API surface.

Tracks breaking / additive changes from elliottech/lighter-python ≥1.1.3
and https://t.me/s/lighter_api_updates:

  - Mainnet L2 chainId = 304 (testnet = 300)  ← CRITICAL
  - Market IDs are int64; new markets ≥ 4095 — never infer spot by ID
  - Use market_type (perp|spot|binary_option), not ID ranges
  - open_interest_usd / open_interest_limit on orderBookDetails
  - priceOracleInfo (stale / internal price / caps)
  - WS graceful shutdown event {type: shutdown, close_in_ms}
  - status root endpoint + Better Stack status page
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional
import json
import os
import urllib.error
import urllib.request


# Official L2 signing chain IDs (docs.lighter.xyz Environments)
CHAIN_ID_MAINNET = 304
CHAIN_ID_TESTNET = 300
CHAIN_ID_RH_MAINNET = 466324

MAINNET_REST = "https://mainnet.zklighter.elliot.ai"
TESTNET_REST = "https://testnet.zklighter.elliot.ai"
RH_MAINNET_REST = "https://api.rh.lighter.xyz"

MAINNET_WS = "wss://mainnet.zklighter.elliot.ai/stream"
STATUS_API = "https://status.lighter.xyz/api/v2/status.json"

# Nov 8 2026 mainnet L1CoreMessaging layout (announced Oct/Nov 2026)
L1_PRIORITY_TX_CUTOVER_UTC = datetime(2026, 11, 8, 13, 0, tzinfo=timezone.utc)


@dataclass
class LighterEndpoints:
    rest: str
    ws: str
    chain_id: int
    name: str

    @classmethod
    def from_env(cls) -> "LighterEndpoints":
        explicit = os.environ.get("LIGHTER_BASE_URL", "").rstrip("/")
        chain = int(os.environ.get("LIGHTER_CHAIN_ID", str(CHAIN_ID_MAINNET)))
        if explicit:
            if "testnet" in explicit:
                return cls(explicit, explicit.replace("https://", "wss://") + "/stream", chain or CHAIN_ID_TESTNET, "custom-testnet")
            if "rh.lighter" in explicit or "rh-testnet" in explicit:
                return cls(explicit, explicit.replace("https://", "wss://") + "/stream", chain or CHAIN_ID_RH_MAINNET, "robinhood")
            return cls(explicit, explicit.replace("https://", "wss://") + "/stream", chain or CHAIN_ID_MAINNET, "custom")
        if chain == CHAIN_ID_TESTNET:
            return cls(TESTNET_REST, "wss://testnet.zklighter.elliot.ai/stream", CHAIN_ID_TESTNET, "testnet")
        if chain == CHAIN_ID_RH_MAINNET:
            return cls(RH_MAINNET_REST, "wss://api.rh.lighter.xyz/stream", CHAIN_ID_RH_MAINNET, "robinhood")
        return cls(MAINNET_REST, MAINNET_WS, CHAIN_ID_MAINNET, "mainnet")


def resolve_network_name(chain_id: int) -> str:
    if chain_id == CHAIN_ID_MAINNET:
        return "mainnet"
    if chain_id == CHAIN_ID_TESTNET:
        return "testnet"
    if chain_id == CHAIN_ID_RH_MAINNET:
        return "robinhood-mainnet"
    return f"unknown({chain_id})"


def assert_mainnet_chain_id(chain_id: int) -> None:
    """Raise if someone points mainnet URL at testnet chainId (or vice versa)."""
    if chain_id not in (CHAIN_ID_MAINNET, CHAIN_ID_TESTNET, CHAIN_ID_RH_MAINNET):
        raise ValueError(
            f"Unsupported Lighter chainId={chain_id}. "
            f"Expected mainnet={CHAIN_ID_MAINNET}, testnet={CHAIN_ID_TESTNET}, "
            f"RH={CHAIN_ID_RH_MAINNET}."
        )


def _http_get_json(url: str, timeout: float = 15.0, verify_ssl: bool = True) -> dict:
    ctx = None
    if not verify_ssl:
        import ssl
        ctx = ssl._create_unverified_context()
    req = urllib.request.Request(url, headers={"User-Agent": "death-mode-v2/lighter-compat"})
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        return json.loads(resp.read().decode())


@dataclass
class MarketSnapshot:
    symbol: str
    market_id: int
    market_type: str
    mark_price: float
    index_price: float
    open_interest: float
    open_interest_usd: float
    open_interest_limit: float
    is_frozen: bool
    min_base_amount: float
    maker_fee: float
    taker_fee: float
    raw: dict = field(default_factory=dict, repr=False)

    @property
    def oi_utilization(self) -> Optional[float]:
        if self.open_interest_limit <= 0:
            return None
        return self.open_interest / self.open_interest_limit

    @property
    def is_perp(self) -> bool:
        return self.market_type == "perp"


def parse_market(row: dict) -> MarketSnapshot:
    return MarketSnapshot(
        symbol=str(row.get("symbol") or ""),
        market_id=int(row.get("market_id") or row.get("market_index") or 0),
        market_type=str(row.get("market_type") or "unknown"),
        mark_price=float(row.get("mark_price") or 0),
        index_price=float(row.get("index_price") or 0),
        open_interest=float(row.get("open_interest") or 0),
        open_interest_usd=float(row.get("open_interest_usd") or 0),
        open_interest_limit=float(row.get("open_interest_limit") or 0),
        is_frozen=bool(row.get("is_frozen")),
        min_base_amount=float(row.get("min_base_amount") or 0),
        maker_fee=float(row.get("maker_fee") or 0),
        taker_fee=float(row.get("taker_fee") or 0),
        raw=row,
    )


@dataclass
class LighterHealth:
    ok: bool
    network: str
    chain_id: int
    rest_status: Optional[int]
    exchange_ok: bool
    status_page: Optional[str]
    market_count: int
    perp_count: int
    frozen_count: int
    high_oi_markets: list[str]
    stale_oracle_symbols: list[str]
    warnings: list[str]
    l1_cutover_pending: bool
    meta: dict[str, Any] = field(default_factory=dict)


class LighterApiClient:
    """Thin sync REST helper for ops / health / pair filtering."""

    def __init__(self, endpoints: Optional[LighterEndpoints] = None, verify_ssl: Optional[bool] = None):
        self.endpoints = endpoints or LighterEndpoints.from_env()
        if verify_ssl is None:
            verify_ssl = os.environ.get("LIGHTER_SKIP_SSL_VERIFY", "0") != "1"
        self.verify_ssl = verify_ssl

    def _get(self, path: str) -> dict:
        url = f"{self.endpoints.rest}{path}"
        return _http_get_json(url, verify_ssl=self.verify_ssl)

    def root_status(self) -> dict:
        return self._get("/")

    def system_config(self) -> dict:
        return self._get("/api/v1/systemConfig")

    def order_book_details(self) -> list[MarketSnapshot]:
        data = self._get("/api/v1/orderBookDetails")
        rows = data.get("order_book_details") or []
        return [parse_market(r) for r in rows]

    def price_oracle_info(self) -> dict:
        try:
            return self._get("/api/v1/priceOracleInfo")
        except Exception as e:
            return {"error": str(e)}

    def tradable_perps(
        self,
        *,
        symbols: Optional[set[str]] = None,
        max_oi_util: float = 0.85,
        exclude_frozen: bool = True,
    ) -> list[MarketSnapshot]:
        """Filter using market_type — never market_id ranges."""
        out = []
        for m in self.order_book_details():
            if not m.is_perp:
                continue
            if symbols and m.symbol not in symbols:
                continue
            if exclude_frozen and m.is_frozen:
                continue
            util = m.oi_utilization
            if util is not None and util >= max_oi_util:
                continue
            out.append(m)
        return out

    def audit(self) -> LighterHealth:
        warnings: list[str] = []
        assert_mainnet_chain_id(self.endpoints.chain_id)

        rest_status = None
        exchange_ok = False
        try:
            root = self.root_status()
            rest_status = int(root.get("status") or root.get("code") or 0)
            exchange_ok = rest_status == 200
        except Exception as e:
            warnings.append(f"root status failed: {e}")

        markets: list[MarketSnapshot] = []
        try:
            markets = self.order_book_details()
        except Exception as e:
            warnings.append(f"orderBookDetails failed: {e}")

        perps = [m for m in markets if m.is_perp]
        frozen = [m for m in perps if m.is_frozen]
        high_oi = [
            m.symbol for m in perps
            if (m.oi_utilization or 0) >= 0.85
        ]

        stale_oracle: list[str] = []
        oracle = self.price_oracle_info()
        for row in oracle.get("markets") or []:
            sym = row.get("symbol")
            for side in ("index", "mark"):
                info = row.get(side) or {}
                if info.get("stale") or info.get("awaiting_sources"):
                    if sym and sym not in stale_oracle:
                        stale_oracle.append(sym)

        status_page = None
        try:
            sp = _http_get_json(STATUS_API, timeout=10.0)
            status_page = (sp.get("status") or {}).get("description") or (sp.get("status") or {}).get("indicator")
            if status_page and str(status_page).lower() not in ("none", "operational", "all systems operational"):
                warnings.append(f"status page: {status_page}")
        except Exception:
            # Better Stack pages vary; non-fatal
            status_page = None

        # Wrong chainId default still common in older templates
        if self.endpoints.name == "mainnet" and self.endpoints.chain_id != CHAIN_ID_MAINNET:
            warnings.append(
                f"CRITICAL: mainnet base URL with chainId={self.endpoints.chain_id} "
                f"(expected {CHAIN_ID_MAINNET})"
            )
        if self.endpoints.name == "testnet" and self.endpoints.chain_id != CHAIN_ID_TESTNET:
            warnings.append(
                f"CRITICAL: testnet base URL with chainId={self.endpoints.chain_id} "
                f"(expected {CHAIN_ID_TESTNET})"
            )

        now = datetime.now(timezone.utc)
        cutover_pending = now < L1_PRIORITY_TX_CUTOVER_UTC
        if cutover_pending:
            warnings.append(
                "L1 Priority TX cutover scheduled 2026-11-08 13:00 UTC — "
                "verify signer/SDK ≥1.1.8 before mainnet L1 ops"
            )

        ok = exchange_ok and not any(w.startswith("CRITICAL") for w in warnings)
        return LighterHealth(
            ok=ok,
            network=self.endpoints.name,
            chain_id=self.endpoints.chain_id,
            rest_status=rest_status,
            exchange_ok=exchange_ok,
            status_page=status_page,
            market_count=len(markets),
            perp_count=len(perps),
            frozen_count=len(frozen),
            high_oi_markets=high_oi[:20],
            stale_oracle_symbols=stale_oracle[:20],
            warnings=warnings,
            l1_cutover_pending=cutover_pending,
            meta={
                "rest": self.endpoints.rest,
                "ws": self.endpoints.ws,
                "sdk_min": "1.1.8",
                "market_id_note": "int64; new markets >= 4095; use market_type",
            },
        )


def should_reconnect_on_ws_message(msg: dict) -> tuple[bool, float]:
    """Return (should_close, wait_seconds) for WS shutdown events."""
    if msg.get("type") == "shutdown":
        close_in_ms = float(msg.get("close_in_ms") or 5000)
        return True, max(0.0, close_in_ms / 1000.0)
    return False, 0.0
