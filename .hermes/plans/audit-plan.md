# DEATH MODE v2 — 완전 자율운영 감사 계획

## Phase 1: 전면 감사 (지금)
- [ ] 모든 config JSON 유효성 검사
- [ ] 모든 Python 파일 문법/import 검증
- [ ] Docker Compose 완전성 검증
- [ ] 빠진 템플릿 파일 확인 (lighter_futures.json 등)
- [ ] 백테스트 결과 압축 풀기 및 분석
- [ ] pm.sh 실제 실행 테스트
- [ ] entrypoint_patch.sh 검증

## Phase 2: 격차 해소
- [ ] 누락된 config 템플릿 생성
- [ ] Python import 수정
- [ ] Docker 볼륨/마운트 검증
- [ ] secrets.env 템플릿 보강
- [ ] scripts/ 모든 스크립트 실행 테스트

## Phase 3: 자동화
- [x] 데이터 자동 다운로드 파이프라인
- [x] 봇 상태 자동 복구 (healthcheck + restart)
- [x] P&L 모니터링 강화
- [ ] Webhook 기반 알림

## Phase 3.5: Cardinal 케이지 (자율운영 강화)
- [x] RiskGate — fixed equity risk / min R:R / daily kill switch
- [x] TiltGuard — revenge cooldown / shrink / asset stop block
- [x] DecisionJournal — allow/block/shrink 전부 기록
- [x] DeathModeV2_Cardinal — 눌림목 선호 + 3R/5R 부분익절 + trailing
- [x] health_monitor kill-switch 인식 (재시작 억제)
- [x] pytest for risk_gate + tilt_guard
- [ ] Webhook: kill switch / tilt block 알림
- [ ] 14일 페이퍼 A/B (rules-only vs Cardinal cage)

## Phase 3.6: Lighter API 2026 Q4 정렬
- [x] mainnet chainId 기본값 300 → **304** (치명 버그 수정)
- [x] market_type 기반 필터 (ID≥2048 spot 추론 제거, int64≥4095 대응)
- [x] WS shutdown 이벤트 재연결
- [x] priceOracleInfo / OI utilization / frozen 감사 스크립트
- [x] lighter-sdk≥1.1.8 핀 + stoploss_on_exchange 기본 ON
- [x] LighterMarketData + live verify-data (candles/mark/funding/book/tape→RiskGate)
- [x] RiskGate: R:R float epsilon + max_stake_mult (타이트 ATR 손절 노셔널 캡)
- [ ] position-tied SL/TP grouped orders (SDK create_grouped_orders) Freqtrade 연동
- [ ] historicalTrade / S3 public dataset 백필 파이프라인
- [ ] 2026-11-08 L1CoreMessaging 컷오버 후 L1 priority TX 검증

## Phase 4: 배포
- [ ] Docker Compose 실제 실행 테스트
- [ ] pm.sh 실제 실행 테스트
- [ ] 최종 커밋/푸시
