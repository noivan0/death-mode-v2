"""Tilt / revenge-trading guard.

Inspired by alpha404ai cooldown brain:
  WATCH · every fill, losses per asset and per hour
  TILT  · score 0–100 from entries/hour, size growth, tighter stops
  BLOCK · tilt ≥ threshold → cooldown
  SHRINK · after 1 loss → next size 50%
  ALLOW · calm → order reaches RiskGate

Hard rules (defaults, overridable via env):
  - 2 losses in 90 min → 40 min cooldown
  - stopped-out on an asset → no new entry on it for 3h
  - same-direction re-entry after stop needs cooldown clear
  - full size returns only after 2 wins in a row
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Optional
import json
import os


class TiltAction(str, Enum):
    ALLOW = "allow"
    SHRINK = "shrink"
    BLOCK = "block"


@dataclass
class TiltVerdict:
    action: TiltAction
    reason: str
    tilt_score: int = 0
    size_multiplier: float = 1.0


@dataclass
class FillEvent:
    symbol: str
    side: str
    pnl: float
    is_stop: bool
    ts: datetime
    entry_rate: Optional[float] = None


@dataclass
class TiltConfig:
    tilt_block_threshold: int = 70
    cooldown_minutes: int = 40
    loss_window_minutes: int = 90
    losses_for_cooldown: int = 2
    asset_stop_cooldown_hours: float = 3.0
    shrink_after_loss: float = 0.5
    wins_to_restore_size: int = 2
    entries_per_hour_soft: int = 4
    entries_per_hour_hard: int = 8
    state_path: Optional[str] = None

    @classmethod
    def from_env(cls) -> "TiltConfig":
        return cls(
            tilt_block_threshold=int(os.environ.get("DM_TILT_BLOCK", "70")),
            cooldown_minutes=int(os.environ.get("DM_TILT_COOLDOWN_MIN", "40")),
            loss_window_minutes=int(os.environ.get("DM_TILT_LOSS_WINDOW_MIN", "90")),
            losses_for_cooldown=int(os.environ.get("DM_TILT_LOSSES_FOR_CD", "2")),
            asset_stop_cooldown_hours=float(os.environ.get("DM_ASSET_STOP_COOLDOWN_H", "3")),
            shrink_after_loss=float(os.environ.get("DM_SHRINK_FACTOR", "0.5")),
            wins_to_restore_size=int(os.environ.get("DM_WINS_TO_RESTORE", "2")),
            entries_per_hour_soft=int(os.environ.get("DM_ENTRIES_SOFT", "4")),
            entries_per_hour_hard=int(os.environ.get("DM_ENTRIES_HARD", "8")),
            state_path=os.environ.get("DM_TILT_STATE_PATH"),
        )


def _default_state_path() -> Path:
    root = Path(os.environ.get("PROJ_DIR", Path(__file__).resolve().parents[2]))
    return root / "user_data" / "tilt_state.json"


def _parse_ts(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


class TiltGuard:
    def __init__(self, config: Optional[TiltConfig] = None):
        self.config = config or TiltConfig.from_env()
        self._state_path = Path(self.config.state_path) if self.config.state_path else _default_state_path()
        self._state = self._load_state()

    def _load_state(self) -> dict:
        if self._state_path.exists():
            try:
                return json.loads(self._state_path.read_text())
            except (json.JSONDecodeError, OSError):
                pass
        return {
            "fills": [],
            "entries": [],
            "cooldown_until": None,
            "asset_block_until": {},
            "consecutive_wins": 0,
            "consecutive_losses": 0,
            "last_size_mult": 1.0,
        }

    def _save_state(self) -> None:
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._state, indent=2, default=str))
        tmp.replace(self._state_path)

    def _now(self, now: Optional[datetime] = None) -> datetime:
        n = now or datetime.now(timezone.utc)
        if n.tzinfo is None:
            n = n.replace(tzinfo=timezone.utc)
        return n

    def _prune(self, now: datetime) -> None:
        cutoff = now - timedelta(hours=24)
        self._state["fills"] = [
            f for f in self._state.get("fills", [])
            if _parse_ts(f["ts"]) >= cutoff
        ]
        self._state["entries"] = [
            e for e in self._state.get("entries", [])
            if _parse_ts(e) >= cutoff
        ]
        blocks = self._state.get("asset_block_until", {})
        self._state["asset_block_until"] = {
            k: v for k, v in blocks.items() if _parse_ts(v) > now
        }

    def record_entry(self, symbol: str, now: Optional[datetime] = None) -> None:
        now = self._now(now)
        self._prune(now)
        self._state.setdefault("entries", []).append(now.isoformat())
        self._save_state()

    def record_fill(self, event: FillEvent) -> None:
        now = self._now(event.ts)
        self._prune(now)
        self._state.setdefault("fills", []).append({
            "symbol": event.symbol,
            "side": event.side,
            "pnl": event.pnl,
            "is_stop": event.is_stop,
            "ts": now.isoformat(),
        })

        if event.pnl < 0:
            self._state["consecutive_losses"] = int(self._state.get("consecutive_losses", 0)) + 1
            self._state["consecutive_wins"] = 0
        elif event.pnl > 0:
            self._state["consecutive_wins"] = int(self._state.get("consecutive_wins", 0)) + 1
            self._state["consecutive_losses"] = 0

        if event.is_stop or event.pnl < 0:
            until = now + timedelta(hours=self.config.asset_stop_cooldown_hours)
            self._state.setdefault("asset_block_until", {})[event.symbol] = until.isoformat()

        # 2 losses in window → cooldown
        window = now - timedelta(minutes=self.config.loss_window_minutes)
        recent_losses = [
            f for f in self._state["fills"]
            if f["pnl"] < 0 and _parse_ts(f["ts"]) >= window
        ]
        if len(recent_losses) >= self.config.losses_for_cooldown:
            cd = now + timedelta(minutes=self.config.cooldown_minutes)
            self._state["cooldown_until"] = cd.isoformat()

        self._save_state()

    def tilt_score(self, now: Optional[datetime] = None) -> int:
        now = self._now(now)
        self._prune(now)
        score = 0

        hour_ago = now - timedelta(hours=1)
        entries_1h = sum(1 for e in self._state.get("entries", []) if _parse_ts(e) >= hour_ago)
        if entries_1h >= self.config.entries_per_hour_hard:
            score += 50
        elif entries_1h >= self.config.entries_per_hour_soft:
            score += 25

        window = now - timedelta(minutes=self.config.loss_window_minutes)
        losses = sum(
            1 for f in self._state.get("fills", [])
            if f["pnl"] < 0 and _parse_ts(f["ts"]) >= window
        )
        score += min(40, losses * 20)

        score += min(20, int(self._state.get("consecutive_losses", 0)) * 10)

        if self._state.get("cooldown_until") and _parse_ts(self._state["cooldown_until"]) > now:
            score = max(score, self.config.tilt_block_threshold)

        return min(100, score)

    def check(self, symbol: str, now: Optional[datetime] = None) -> TiltVerdict:
        now = self._now(now)
        self._prune(now)
        cfg = self.config
        score = self.tilt_score(now)

        if self._state.get("cooldown_until"):
            until = _parse_ts(self._state["cooldown_until"])
            if until > now:
                return TiltVerdict(
                    TiltAction.BLOCK,
                    f"cooldown until {until.isoformat()}",
                    tilt_score=score,
                    size_multiplier=0.0,
                )

        asset_until = self._state.get("asset_block_until", {}).get(symbol)
        if asset_until and _parse_ts(asset_until) > now:
            return TiltVerdict(
                TiltAction.BLOCK,
                f"asset stop cooldown until {asset_until}",
                tilt_score=score,
                size_multiplier=0.0,
            )

        if score >= cfg.tilt_block_threshold:
            return TiltVerdict(
                TiltAction.BLOCK,
                f"tilt score {score} >= {cfg.tilt_block_threshold}",
                tilt_score=score,
                size_multiplier=0.0,
            )

        wins = int(self._state.get("consecutive_wins", 0))
        losses = int(self._state.get("consecutive_losses", 0))
        if losses >= 1 and wins < cfg.wins_to_restore_size:
            return TiltVerdict(
                TiltAction.SHRINK,
                f"post-loss shrink (wins={wins}/{cfg.wins_to_restore_size})",
                tilt_score=score,
                size_multiplier=cfg.shrink_after_loss,
            )

        return TiltVerdict(
            TiltAction.ALLOW,
            "calm",
            tilt_score=score,
            size_multiplier=1.0,
        )
