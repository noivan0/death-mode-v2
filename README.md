# 💀 DEATH MODE v2

> **NFI X7 기반 Hyperliquid + Lighter 자율 트레이딩 시스템**  
> Strategy: NostalgiaForInfinityX7 · Exchange: Hyperliquid + Lighter DEX

---

## 개요

DEATH MODE v2는 검증된 NFI X7(NostalgiaForInfinityX7) 전략을 기반으로  
Hyperliquid와 Lighter 두 DEX를 동시에 운용하는 Freqtrade 기반 트레이딩 봇입니다.

- **HL 봇**: Hyperliquid Perpetual Futures (9페어, USDC)
- **Lighter 봇**: Lighter DEX ZK-rollup Perpetual (BTC/ETH/SOL, USDC)
- **전략 버전**: `death-v2-nfi-1.2`

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

### 2. 환경변수 설정

```bash
cp secrets.env.template secrets.env
# secrets.env 편집:
# HYPERLIQUID_WALLET_ADDRESS=0x...
# HYPERLIQUID_PRIVATE_KEY=0x...
# LIGHTER_PRIVATE_KEY=0x...
# LIGHTER_ACCOUNT_INDEX=...
# LIGHTER_API_KEY_INDEX=...
```

### 3. Config 준비

```bash
# HL 봇 config
cp config/hl_paper_ict.json.template config/hl_paper_ict.json
# ← privateKey, walletAddress 직접 입력 또는 envsubst 사용

# Lighter는 config/lighter_futures.json 그대로 사용 가능
# (privateKey는 환경변수 LIGHTER_PRIVATE_KEY로 주입)
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
|---|---|
| v1.0 | NFI X7 초기 통합, ccxt options 보존 |
| v1.1 | privateKey 마스킹, JWT 강화, API 127.0.0.1 바인딩 |
| v1.2 | TON 블랙리스트, startup_candle_count config화, SSL 환경변수 조건부, _mask_sensitive list 지원 |

---

## 주의사항

- `secrets.env`, `config/hl_paper_ict.json` 등 민감 파일은 `.gitignore`에 등록됨
- `NostalgiaForInfinityX7.py`는 [별도 저장소](https://github.com/iterativv/NostalgiaForInfinity)에서 클론
- Lighter 데이터는 HL 데이터 심링크 방식 (ccxt lighter fetchFundingRateHistory 미지원)
- HMG 사내망 환경: `LIGHTER_SKIP_SSL_VERIFY=1` 설정 필요

---

## 라이선스

MIT License — NFI X7 전략은 원저작자 [iterativv](https://github.com/iterativv) 라이선스 따름.
