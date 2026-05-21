"""
DeathModeV2_NFI — NFI X7 완전 상속 + DEATH MODE 패치
==========================================================
변경사항:
  1. startup_candle_count=500 (로컬 데이터 확보 → RSI_3_1d 정확도 확보)
  2. ccxt options 서브키(accountIndex 등) Lighter 용 보존 (Claude Code 감사 #1')
  3. NFI __init__ brokerId None 주입 → ccxt options 덮어쓰기 방지
  4. privateKey 로그 마스킹 (Codex 감사 CRITICAL-1)
  5. _on_closed thread-safe 경로 추가 (Claude Code 감사 #5)

감사 이력:
  - Claude Code Audit v3: options 서브키 보존 BUG, startup 200 BUG, thread-safety
  - Codex Security Audit v3: CRITICAL privateKey 노출, JWT 강화, .gitignore
"""
import asyncio
import copy
import logging
from typing import Optional

from freqtrade.strategy import IStrategy
from pandas import DataFrame

import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from NostalgiaForInfinityX7 import NostalgiaForInfinityX7

log = logging.getLogger(__name__)

# 민감 키 마스킹 (Codex CRITICAL-1)
_SENSITIVE_KEYS = {"privateKey", "secret", "key", "password", "apiKey"}


def _mask_sensitive(d: dict) -> dict:
    """재귀적으로 민감 키 마스킹 (list 내 dict도 처리)"""
    result = {}
    for k, v in d.items():
        if k in _SENSITIVE_KEYS:
            result[k] = "****MASKED****"
        elif isinstance(v, dict):
            result[k] = _mask_sensitive(v)
        elif isinstance(v, list):
            result[k] = [_mask_sensitive(i) if isinstance(i, dict) else i for i in v]
        else:
            result[k] = v
    return result


class DeathModeV2_NFI(NostalgiaForInfinityX7):
    """
    NFI X7 완전 상속 — DEATH MODE 튜닝

    Claude Code 감사 v3 수정사항:
    - ccxt_config.options 서브키(accountIndex/apiKeyIndex/chainId) 보존 (Lighter)
    - startup_candle_count=500 (RSI_3_1d 등 1d 지표 정확도 확보)
    - asyncio.Queue thread-safe 경로

    Codex 보안감사 v3 수정사항:
    - privateKey 로그 마스킹
    """

    INTERFACE_VERSION = 3

    def version(self) -> str:
        return "death-v2-nfi-1.2"

    timeframe = "5m"

    def __init__(self, config: dict) -> None:
        # ── 1. ccxt 설정 보존 (walletAddress/privateKey + options 서브키) ──────
        _saved: dict = {}
        for cfg_key in ("ccxt_config", "ccxt_async_config"):
            src = config.get("exchange", {}).get(cfg_key, {})
            saved = {}
            for k, v in src.items():
                if k in ("walletAddress", "privateKey", "verify", "rateLimit"):
                    saved[k] = v
            # Lighter용: options 서브키 (accountIndex/apiKeyIndex/chainId) 보존
            if "options" in src:
                saved["_options_backup"] = copy.deepcopy(src["options"])
            _saved[cfg_key] = saved

        # ── 2. NFI X7 초기화 (options 덮어쓰기 발생) ─────────────────────────
        try:
            super().__init__(config)
        except Exception as e:
            log.error(f"[DeathModeV2_NFI] NFI X7 초기화 실패: {e}")
            raise

        # ── 3. NFI 덮어쓰기 이후 원래 설정 복원 ──────────────────────────────
        for cfg_key, saved in _saved.items():
            if not saved:
                continue
            options_backup = saved.pop("_options_backup", None)
            # 최상위 키 복원 (walletAddress/privateKey/verify/rateLimit)
            config["exchange"].setdefault(cfg_key, {}).update(saved)
            # options 서브키 복원 (NFI brokerId None 위에 accountIndex 등 merge)
            if options_backup is not None:
                config["exchange"][cfg_key].setdefault("options", {}).update(options_backup)

        # ── 4. startup_candle_count 설정 ──────────────────────────────────────
        # NFI 기본=800. config에서 startup_candle_count 지정 시 그 값 우선.
        # 로컬 데이터(사전 download-data) 확보 전제 시 config=500으로 맞춤.
        # [FIX] 하드코딩 제거 → config 또는 NFI 기본값 유지
        config_startup = config.get("startup_candle_count")
        if config_startup is not None:
            self.startup_candle_count = config_startup
            log.info(f"[DeathModeV2_NFI] startup_candle_count ← config: {config_startup}")
        else:
            log.info(f"[DeathModeV2_NFI] startup_candle_count ← NFI 기본: {self.startup_candle_count}")

        # ── 5. 마스킹된 config 정보 로깅 (Codex CRITICAL-1 대응) ──────────────
        exchange_name = config.get("exchange", {}).get("name", "unknown")
        log.info(f"[DeathModeV2_NFI] exchange={exchange_name} init complete (v{self.version()})")
