# DEATH MODE v2 — Agent Workspace Guide

이 문서는 Hermes Agent가 이 프로젝트를 자율운영할 때 참조하는 메타데이터입니다.

## 프로젝트 경로
- `/mnt/d/death-mode-v2` — WSL 로컬 작업 디렉토리
- `https://github.com/noivan0/death-mode-v2` — GitHub (private)

## 시작 명령어

```bash
# 페이퍼 트레이딩 (HL)
freqtrade trade --config config/hl_paper_ict.json --strategy DeathModeV2_NFI --strategy-path user_data/strategies/

# 페이퍼 트레이딩 (Lighter)
freqtrade trade --config config/lighter_futures.json --strategy DeathModeV2_NFI --strategy-path user_data/strategies/

# pm.sh
./pm.sh start [lighter|hyperliquid|all]
./pm.sh status
./pm.sh logs [exchange]

# Docker
docker compose up -d
docker compose --profile hyperliquid up -d
```

## 전략 선택 가이드

| 용도 | 추천 전략 | 이유 |
|------|-----------|------|
| 자율운영 (기본) | DeathModeV2_Cardinal | Risk Gate + Tilt + 고정R 사이징 + 3R/5R 부분익절 |
| 실전 (안정) | DeathModeV2_Base | stoploss 7%, trailing, DCA 강제청산 |
| 실전 (공격) | DeathModeV2_SharpV2 | Grinding 차단, ROI 타이트 |
| 페이퍼/백테스트 | DeathModeV2_NFI | NFI X7 순수 상속 (참조용) |
| 분석 | DeathModeV2_ICT / OrderFlow | ICT 기법 / CVD 로깅 |

## Cardinal 케이지 (자율운영 필수)

참조: alpha404ai Cardinal desk + Martin Luk 손익 비대칭 (dubidubabap).

- `core/risk/risk_gate.py` — 사이즈·kill switch·min R:R (LLM 금지)
- `core/risk/tilt_guard.py` — 복수 보복매매 cooldown / shrink
- `core/risk/journal.py` — allow/block/shrink 전부 기록
- 상태: `python bot.py risk` 또는 `python scripts/risk_status.py`
- 환경변수: `DM_MAX_RISK` (기본 0.005), `DM_MAX_DAY_LOSS` (0.02), `DM_MIN_RR` (2.0), `DM_STRATEGY`

## 설정 파일

모든 민감 설정은 `.env` (gitignore) → `config/*.json` expand 구조.
`secrets.env`는 `.env` 심볼릭 링크(하위호환). 관리: `bash scripts/manage_env.sh status|expand`.
`setup.sh` 실행 시 `.env.template`에서 자동 생성됨.

## 크론잡
- `death-mode-daily-pnl` — 24시간 간격 P&L 리포트

## 주의사항
- NostalgiaForInfinityX7.py는 3.4MB 대용량 파일 (외부 repo에서 클론)
- Lighter 오더북은 USDC 기반 (USDT 아님)
- **Lighter mainnet L2 chainId = 304** (testnet = 300). 템플릿 기본값 304 유지
- `lighter-sdk>=1.1.8` 필수 (int64 market_id, WS shutdown, modify-order)
- market_id로 spot/perp 추론 금지 → `market_type` 필드 사용 (신규 마켓 ≥4095)
- HMG 사내망: `LIGHTER_SKIP_SSL_VERIFY=1` 필수
- 2026-11-08 13:00 UTC: mainnet L1 Priority TX → L1CoreMessaging 컷오버 예정
- 백테스트 데이터: 2023-01-01 ~ 2025-05-01 (2.3년)
- 라이브 전 체크: `python bot.py lighter-audit` + `python bot.py risk` + dry_run 페이퍼
