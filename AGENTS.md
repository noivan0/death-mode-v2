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
| 실전 (안정) | DeathModeV2_Base | stoploss 7%, trailing, DCA 강제청산 |
| 실전 (공격) | DeathModeV2_SharpV2 | Grinding 차단, ROI 타이트 |
| 페이퍼/백테스트 | DeathModeV2_NFI | NFI X7 순수 상속 (참조용) |
| 분석 | DeathModeV2_ICT / OrderFlow | ICT 기법 / CVD 로깅 |

## 설정 파일

모든 민감 설정은 `secrets.env` → `config/*.json` envsubst 구조.
`setup.sh` 실행 시 템플릿에서 자동 생성됨.

## 크론잡
- `death-mode-daily-pnl` — 24시간 간격 P&L 리포트

## 주의사항
- NostalgiaForInfinityX7.py는 3.4MB 대용량 파일 (외부 repo에서 클론)
- Lighter 오더북은 USDC 기반 (USDT 아님)
- HMG 사내망: `LIGHTER_SKIP_SSL_VERIFY=1` 필수
- 백테스트 데이터: 2023-01-01 ~ 2025-05-01 (2.3년)
