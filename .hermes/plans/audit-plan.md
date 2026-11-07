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

## Phase 4: 배포
- [ ] Docker Compose 실제 실행 테스트
- [ ] pm.sh 실제 실행 테스트
- [ ] 최종 커밋/푸시
