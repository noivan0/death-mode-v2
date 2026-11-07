# 💀 DEATH MODE v2

> **NFI X7 기반 Hyperliquid + Lighter 자율 트레이딩 시스템**  
> Strategy: NostalgiaForInfinityX7 · Exchange: Hyperliquid + Lighter DEX

---

## 개요

DEATH MODE v2는 검증된 NFI X7(NostalgiaForInfinityX7) 전략을 기반으로  
Hyperliquid와 Lighter 두 DEX를 동시에 운용하는 Freqtrade 기반 트레이딩 봇입니다.

- **HL 봇**: Hyperliquid Perpetual Futures (9페어, USDC)
- **Lighter 봇**: Lighter DEX ZK-rollup Perpetual (BTC/ETH/SOL, USDC)
- **전략 버전**: `death-v2-cardinal-1.0` (자율운영) / `death-v2-nfi-1.2` (참조)

---

## 핵심 특징

| 항목 | 내용 |
|---|---|
| 기반 전략 | NFI X7 (Nostalgia For Infinity X7) |
| 거래소 | Hyperliquid (isolated margin) + Lighter (cross margin) |
| 타임프레임 | 5m (메인) + 15m / 1h / 4h / 1d (멀티TF) |
| stake_currency | USDC |
| 진입 방식 | dip-buy (RSI 과매수 시 대기, 하락 반전 시 진입) |
| ICT 기법 | enter_tag 로깅 (FVG, OB, Liquidity Grab 등) |
| startup_candle_count | config 기준 (HL=500, Lighter=200) |

---

## 프로젝트 구조

```
death-mode-v2/
├── config/                         # 거래소 config (민감 파일 제외, .gitignore)
│   ├── hl_paper_ict.json.template  # HL 봇 config 템플릿
│   ├── lighter_futures.json        # Lighter 봇 config
│   └── pairlist-lighter-usdc.json  # Lighter 페어리스트
├── core/
│   ├── exchange/
│   │   ├── lighter_exchange.py     # Lighter Freqtrade Exchange 서브클래스
│   │   └── lighter_ws_ohlcv.py     # Lighter WebSocket OHLCV 클라이언트
│   └── strategy/ict/               # ICT 기법 모듈 (로깅 전용)
├── exchange_patch/
│   ├── lighter.py                  # Lighter Exchange 패치 (v7)
│   └── lighter_sdk.py              # Lighter SDK 래퍼
├── user_data/strategies/
│   ├── DeathModeV2_NFI.py          # 메인 전략 (NFI X7 상속)
│   └── NostalgiaForInfinityX7.py   # ← 별도 클론 필요 (아래 참고)
├── scripts/                        # 데이터 다운로드 스크립트
├── secrets.env.template            # 환경변수 템플릿
└── docker-compose.yml              # Docker 운영
```

---

## 빠른 시작

### 1. 사전 준비

```bash
# Freqtrade 설치 (venv 권장)
git clone https://github.com/freqtrade/freqtrade.git
cd freqtrade && ./setup.sh -i

# NFI X7 전략 클론 (별도 저장소)
git clone https://github.com/iterativv/NostalgiaForInfinity.git
cp NostalgiaForInfinity/NostalgiaForInfinityX7.py death-mode-v2/user_data/strategies/
```

### 2. 환경변수 설정 (`.env`)

```bash
cp .env.template .env && chmod 600 .env
# .env 편집 후:
bash scripts/manage_env.sh status
bash scripts/manage_env.sh expand
# secrets.env 는 .env 심볼릭 링크(하위호환)
```

### 3. Config 준비

```bash
# HL 봇 config
cp config/hl_paper_ict.json.template config/hl_paper_ict.json
# ← privateKey, walletAddress 직접 입력 또는 manage_env expand

# Lighter futures config는 expand로 생성
# (privateKey는 .env 의 LIGHTER_PRIVATE_KEY / LIGHTER_API_SECRET)
```

### 4. Lighter Exchange 등록

```bash
# Freqtrade에 Lighter exchange 등록
cp exchange_patch/lighter.py $(pip show freqtrade | grep Location | awk '{print $2}')/freqtrade/exchange/lighter.py

# __init__.py에 import 추가
echo "from freqtrade.exchange.lighter import Lighter" >> \
  $(pip show freqtrade | grep Location | awk '{print $2}')/freqtrade/exchange/__init__.py
```

### 5. 데이터 다운로드

```bash
# HL 데이터 (9페어 × 5TF)
freqtrade download-data --config config/hl_paper_ict.json \
  --days 30 --timeframes 5m 15m 1h 4h 1d

# Lighter 데이터 (HL 데이터 심링크)
ln -s $(pwd)/user_data/data/hyperliquid/futures/BTC_USDC_USDC-*.json \
  user_data/data/lighter/futures/
```

### 6. 봇 실행

```bash
# HL 봇 (페이퍼 트레이딩)
freqtrade trade \
  --config config/hl_paper_ict.json \
  --strategy DeathModeV2_NFI \
  --strategy-path user_data/strategies/

# Lighter 봇 (페이퍼 트레이딩)
freqtrade trade \
  --config config/lighter_futures.json \
  --strategy DeathModeV2_NFI \
  --strategy-path user_data/strategies/
```

---

## 환경변수 전체 목록

| 변수명 | 필수 | 설명 |
|---|---|---|
| `HYPERLIQUID_WALLET_ADDRESS` | 실전 | HL 지갑 주소 (0x...) |
| `HYPERLIQUID_PRIVATE_KEY` | 실전 | HL EVM 개인키 |
| `LIGHTER_PRIVATE_KEY` | 실전 | Lighter EVM 개인키 |
| `LIGHTER_ACCOUNT_INDEX` | 선택 | Lighter 계정 인덱스 (기본 0) |
| `LIGHTER_API_KEY_INDEX` | 선택 | Lighter API 키 인덱스 (기본 0) |
| `LIGHTER_SKIP_SSL_VERIFY` | 선택 | `1` = SSL 검증 비활성화 (HMG 사내망 전용) |

---

## 실전 전환 체크리스트

```
□ secrets.env에 실제 키 설정
□ config의 dry_run: false 변경
□ stake_amount: "unlimited" → 고정값 (예: 500)
□ stoploss: -0.99 → -0.15 (NFI 기본값 override)
□ HL: stoploss_on_exchange: false → true
□ tradable_balance_ratio: 0.99 → 0.5 (보수적 운용)
```

---

## 감사 이력

| 버전 | 내용 |
|------|------|
| v1.2 | TON 블랙리스트, startup_candle_count config화, SSL 환경변수 조건부, _mask_sensitive list 지원 |
| v1.1 | privateKey 마스킹, JWT 강화, API 127.0.0.1 바인딩 |
| v1.0 | NFI X7 초기 통합, ccxt options 보존 |

---

## 자율운영 (Hermes Agent)

이 프로젝트는 Hermes Agent 기반 자율운영을 지원합니다:

- **프로젝트 등록**: `.hermes/project.yml` — 전략/거래소/상태 정의
- **CI/CD**: GitHub Actions (lint + risk tests + import 검증 + config 유효성 검사)
- **데일리 리포트**: `python scripts/daily_report.py` — 일일 P&L · payoff · expectancy(R)
- **리스크 케이지**: `python bot.py risk` — kill switch / tilt / journal
- **헬스체크**: `python scripts/health_monitor.py --check-once` (kill switch 시 재시작 억제)
- **크론잡**: Hermes cron을 통한 주기적 P&L 모니터링 가능

### Cardinal 케이지 (권장 자율운영 스택)

| 레이어 | 역할 | 구현 |
|--------|------|------|
| Signal | NFI 진입 신호 | `DeathModeV2_Cardinal` |
| TiltGuard | 보복매매 cooldown / shrink | `core/risk/tilt_guard.py` |
| RiskGate | 고정 % 리스크 사이징 · min R:R · kill switch | `core/risk/risk_gate.py` |
| Journal | allow/block/shrink 전부 기록 | `core/risk/journal.py` |
| Exit | +3R/+5R 부분익절 후 trailing | Cardinal `adjust_trade_position` |

원칙: **에이전트/LLM은 사이즈를 정하지 않는다. Python RiskGate만 정한다.**

```bash
# 자율운영 기동 전 — Lighter API 감사 + 실데이터 시그널/게이트 검증
python bot.py lighter-audit
python bot.py verify-data          # candles/funding/book/OI → Cardinal RiskGate

# 페이퍼 (dry_run) — Lighter + DeathModeV2_Cardinal
bash scripts/start_paper.sh
# 또는: python bot.py start -e lighter
python bot.py risk
python bot.py health
# API: http://127.0.0.1:8091  (death / deathpaper)
```

> **실전 적용 전 필수:** `secrets.env`의 `LIGHTER_CHAIN_ID=304`(mainnet), `lighter-sdk>=1.1.8`,
> config `dry_run: true`로 페이퍼 검증 후 `false` 전환. Cardinal 케이지만으로 실전 완료가 아님.

### CI 상태

`main` 브랜치 푸시 시 자동 실행:
1. Flake8 린트 (심각도 E9/F63/F7/F82)
2. Cardinal risk / tilt pytest
3. 전략 import 검증
4. JSON config 유효성 검사

---

## 주의사항

- `secrets.env`, `config/hl_paper_ict.json` 등 민감 파일은 `.gitignore`에 등록됨
- `NostalgiaForInfinityX7.py`는 [별도 저장소](https://github.com/iterativv/NostalgiaForInfinity)에서 클론
- Lighter 데이터는 HL 데이터 심링크 방식 (ccxt lighter fetchFundingRateHistory 미지원)
- HMG 사내망 환경: `LIGHTER_SKIP_SSL_VERIFY=1` 설정 필요

---

## 라이선스

MIT License — NFI X7 전략은 원저작자 [iterativv](https://github.com/iterativv) 라이선스 따름.
