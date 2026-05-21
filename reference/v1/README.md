# Lighter Trading Bot - Agent Team Project

**자동 트레이딩 봇 with Self-Optimizing Agent Team**

---

## 🎯 프로젝트 개요

Lighter DEX에서 자동으로 거래하는 봇 시스템으로, **4개의 전문 Agent가 협업**하여 성과를 지속적으로 최적화합니다.

### 핵심 특징
- ✅ **자동 거래**: FAST 모드 (3-TF: 1m, 5m, 15m)
- ✅ **Agent Team**: QA, Performance, Risk, Strategy
- ✅ **자가 최적화**: Agent들이 서로 협업하며 개선
- ✅ **24/7 모니터링**: Heartbeat + Agent 자동 소환

---

## 👥 Agent Team

### 1. QA Engineer (`agents/qa-engineer.md`)
**역할**: 품질 검증 및 문제 진단

- Runtime 에러 탐지
- 자동 진단 및 해결
- 배포 전 검증
- Known Issues 관리

**트리거**: Heartbeat, 배포 전, 사용자 요청

---

### 2. Performance Monitor (`agents/performance-monitor.md`)
**역할**: 성과 추적 및 분석

- 일일/주간 리포트 생성
- Win Rate, Sharpe Ratio 추적
- 패턴 발견 및 인사이트
- 최적화 제안

**트리거**: 매일 00:00, 주간 일요일

---

### 3. Risk Manager (`agents/risk-manager.md`)
**역할**: 리스크 관리 및 자본 보호

- Drawdown 모니터링
- Emergency Stop 트리거
- 포지션 사이즈 최적화
- VaR, Kelly Criterion

**트리거**: 매일 06:00, 손실 > 5%, Drawdown > 8%

---

### 4. Strategy Optimizer (`agents/strategy-optimizer.md`)
**역할**: 전략 최적화

- 파라미터 튜닝 (Confidence, EMA, RSI)
- 백테스트 실행
- Walk-Forward 분석
- 지속적 개선

**트리거**: 주간 일요일, Win Rate < 70%

---

## 🔄 Agent 협업 흐름

### 예시: 성능 저하 시
```
1. Performance Monitor
   "Win Rate 65% (목표 75%)"
   
2. Strategy Optimizer 자동 소환
   파라미터 최적화 실행
   
3. QA Engineer
   백테스트 검증
   
4. Risk Manager
   리스크 평가 (VaR, Drawdown)
   
5. 승인 후 적용
   
6. Performance Monitor
   효과 측정 (24-48시간)
```

### 예시: 에러 발생 시
```
1. Heartbeat
   "NaN 에러 탐지"
   
2. QA Engineer 소환
   진단: calculateConfidence3TF 타입 불일치
   
3. Performance Monitor
   영향도 분석: 거래 완전 중단
   
4. Risk Manager
   긴급도 판단: Critical
   
5. QA Engineer
   수정 적용: .total 속성 추출
   
6. All Agents
   검증 및 모니터링
```

---

## 📁 프로젝트 구조

```
crypto-trading-agents/
├── README.md              # 이 파일
├── WORKFLOW.md            # Agent 워크플로우
├── TOOLS.md               # 설정 및 도구
├── ARCHITECTURE.md        # 시스템 구조
│
├── agents/                # Agent Team
│   ├── qa-engineer.md
│   ├── performance-monitor.md
│   ├── risk-manager.md
│   └── strategy-optimizer.md
│
├── src/                   # 소스 코드
│   ├── multi-market-bot-top20.ts
│   └── lib/
│
├── logs/                  # 로그
├── reports/               # Agent 리포트
│   ├── daily/
│   ├── weekly/
│   ├── risk/
│   └── optimization/
│
└── scripts/               # Agent 실행 스크립트
    ├── performance-daily-report.ts
    ├── risk-daily-check.ts
    └── strategy-optimize-weekly.ts
```

---

## 🚀 빠른 시작

### 1. 설정 확인
```bash
# TOOLS.md에서 API Keys, 환경 변수 확인
cat TOOLS.md
```

### 2. Bot 실행
```bash
# Trading Bot 시작
LIVE_TRADING=true nohup npx tsx src/multi-market-bot-top20.ts > logs/multi-market.log 2>&1 &
echo $! > /tmp/live-bot-pid
```

### 3. Agent 소환 테스트
```
"QA Engineer 소환해서 현재 상태 체크해줘"
"Performance Monitor로 오늘 성과 분석해줘"
"Risk Manager로 리스크 체크해줘"
```

---

## 📊 자가 최적화 사이클

```
┌─────────────────────────────────────────┐
│                                         │
│  1. Monitor (Performance Monitor)       │
│     실시간 성과 추적                     │
│     ↓                                   │
│  2. Detect (QA + Performance)           │
│     문제 또는 개선 기회 발견             │
│     ↓                                   │
│  3. Analyze (Strategy Optimizer)        │
│     최적 파라미터 계산                   │
│     ↓                                   │
│  4. Validate (Risk Manager + QA)        │
│     리스크 평가 + 백테스트               │
│     ↓                                   │
│  5. Apply (QA Engineer)                 │
│     코드 수정 및 배포                    │
│     ↓                                   │
│  6. Monitor (반복)                      │
│     효과 측정 및 피드백                  │
│     ↓                                   │
└─────┴───────────────────────────────────┘
      (Continuous Improvement Loop)
```

---

## 🎯 현재 상태

### Bot
- **모드**: LIVE TRADING (Real Money!)
- **마켓**: TOP 20
- **Signal**: FAST (3-TF)
- **자본**: $580.32

### Agent Team
- ✅ QA Engineer: 활성화
- ✅ Performance Monitor: 일일 리포트 설정 필요
- ✅ Risk Manager: 일일 체크 설정 필요
- ✅ Strategy Optimizer: 주간 최적화 설정 필요

---

## 📈 목표

### 단기 (1주)
- [ ] 4개 Agent 모두 자동화
- [ ] 일일 리포트 생성
- [ ] Agent 협업 검증

### 중기 (1개월)
- [ ] Win Rate > 75%
- [ ] Sharpe Ratio > 1.5
- [ ] 완전 자동 최적화

### 장기 (6개월)
- [ ] BASE 모드 추가 (6-TF)
- [ ] ENHANCED 모드 (ML/AI)
- [ ] ALL MARKETS 확장

---

## 🔧 주요 명령어

### Bot 관리
```bash
# 상태 확인
ps -p $(cat /tmp/live-bot-pid)

# 로그
tail -f logs/multi-market.log

# 재시작
kill $(cat /tmp/live-bot-pid)
LIVE_TRADING=true nohup npx tsx src/multi-market-bot-top20.ts > logs/multi-market.log 2>&1 &
echo $! > /tmp/live-bot-pid
```

### Agent 소환
```
"QA Engineer 소환해서 [문제] 진단해줘"
"Performance Monitor로 성과 분석해줘"
"Risk Manager로 리스크 체크해줘"
"Strategy Optimizer로 최적화해줘"
```

---

## 📚 문서

- **WORKFLOW.md** - Agent 워크플로우 및 협업 프로토콜
- **TOOLS.md** - API Keys, 설정, 스크립트
- **ARCHITECTURE.md** - 시스템 구조
- **agents/*.md** - 각 Agent 상세 스펙

---

## 🚨 Emergency

### Trading Bot 즉시 중단
```bash
kill $(cat /tmp/live-bot-pid)
```

### Emergency Stop (Risk Manager)
```
"Risk Manager 소환해서 Emergency Stop 실행해줘"
```

---

## 📞 문제 해결

### Bot이 거래하지 않음
```
1. "QA Engineer 소환해서 Trading Bot 진단해줘"
2. logs/multi-market.log 확인
3. Confidence 계산 확인
```

### 성과가 낮음
```
1. "Performance Monitor로 성과 분석해줘"
2. "Strategy Optimizer로 최적화해줘"
```

### 손실이 큼
```
1. "Risk Manager로 긴급 리스크 체크해줘"
2. Emergency Stop 고려
```

---

## 🎓 핵심 원칙

### 1. Agent Team Framework
**모든 프로젝트**는 Agent Team 구조로 시작합니다.

```
/project/
  ├── agents/        # 프로젝트 전용 에이전트
  ├── WORKFLOW.md    # 워크플로우
  └── TOOLS.md       # 설정
```

### 2. Self-Optimization
Agent들이 서로 협업하며 **스스로 성능을 개선**합니다.

### 3. Continuous Monitoring
**24/7 모니터링** + 자동 개입 + 사용자 알림

---

_Agent Team Framework v1.0_
_Created: 2026-03-07_
_Status: Production Ready 🚀_
