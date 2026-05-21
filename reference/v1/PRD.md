# Product Requirements Document (PRD)
# Crypto Trading Agents - Multi-Timeframe EMA Strategy

**Version**: 1.0  
**Date**: 2026-03-06  
**Status**: Draft  
**Author**: ProductLead Agent

---

## 📋 Table of Contents

1. [Executive Summary](#executive-summary)
2. [Product Overview](#product-overview)
3. [Market & Opportunity](#market--opportunity)
4. [User Personas](#user-personas)
5. [Technical Requirements](#technical-requirements)
6. [Agent Architecture](#agent-architecture)
7. [Core Features](#core-features)
8. [Trading Strategy](#trading-strategy)
9. [Risk Management](#risk-management)
10. [Success Metrics](#success-metrics)
11. [Development Roadmap](#development-roadmap)
12. [Dependencies & Integrations](#dependencies--integrations)

---

## 📊 Executive Summary

### Vision
Create an autonomous crypto trading bot that leverages multi-timeframe EMA analysis to achieve consistent 80%+ win rates with minimal human intervention, enabling small capital ($100-1,000) to grow exponentially through compound returns.

### Core Value Proposition
- **High Win Rate**: 80%+ through confidence-based filtering
- **Multi-Timeframe Analysis**: 1m/5m/15m synchronized signals
- **Risk-Optimized**: Kelly Criterion + dynamic position sizing
- **Autonomous**: 5-agent team handling analysis → execution → tracking
- **Transparent**: Real-time Telegram notifications + performance tracking

### Target Metrics
| Metric | Target | Stretch Goal |
|--------|--------|--------------|
| Win Rate | 80% | 85% |
| Annual Return | 20-80% | 100%+ |
| Max Drawdown | < 15% | < 10% |
| R/R Ratio | > 2.0 | > 3.0 |
| Trades/Day | 2-5 | 3-7 |

---

## 🎯 Product Overview

### What We're Building

An autonomous trading system composed of 5 specialized AI agents that work together to:

1. **Analyze** market data across multiple timeframes
2. **Calculate** signal confidence (0-100 score)
3. **Generate** trading signals only when confidence ≥ 60%
4. **Execute** trades with optimized position sizing
5. **Track** performance and learn from outcomes

### Why It Matters

**Problem**: Most trading bots fail because they:
- Trade too frequently (noise)
- Don't filter low-confidence signals
- Use single-timeframe analysis (limited context)
- Lack adaptive risk management

**Solution**: Our multi-agent system:
- ✅ Only trades high-confidence setups (60%+)
- ✅ Validates signals across 3 timeframes
- ✅ Adapts position size to confidence level
- ✅ Learns from every trade via Knowledge Base

### Differentiation

| Feature | Traditional Bots | Our System |
|---------|------------------|------------|
| Signal Filtering | Basic (price cross) | Confidence-based (100-point) |
| Timeframes | Single | Multi (1m/5m/15m) |
| Position Sizing | Fixed % | Dynamic (confidence × Kelly) |
| Risk Management | Static stop-loss | Multi-level (timeframe-based) |
| Learning | None | KB-driven pattern recognition |
| Team Structure | Monolithic | 5 specialized agents |

---

## 💼 Market & Opportunity

### Target Market

**Primary Users**:
- Small capital traders ($100-1,000)
- Crypto enthusiasts seeking passive income
- Developers interested in algorithmic trading
- DeFi users on Lighter DEX

**Market Size**:
- Global algorithmic trading market: $19.7B (2024)
- Crypto trading volume: $50B+ daily
- Growing interest in autonomous trading agents

### Competitive Analysis

| Competitor | Strength | Weakness | Our Advantage |
|-----------|----------|----------|---------------|
| 3Commas | UI/UX | Single timeframe | Multi-timeframe confidence |
| Cryptohopper | Social trading | High fees | Open-source, autonomous |
| Manual Trading | Full control | Emotional, slow | Emotionless, fast execution |

### Success Criteria

**Validation Metrics**:
- [ ] Backtest: 70%+ win rate over 3 months historical data
- [ ] Paper Trading: 2 weeks without major losses
- [ ] Testnet: $100 → $120+ in 1 week
- [ ] Mainnet: Positive returns for 30 days

---

## 👥 User Personas

### Persona 1: Small Capital Trader

**Name**: Alex  
**Age**: 28  
**Capital**: $500  
**Goal**: Grow to $5,000 in 6 months  
**Pain Points**:
- Can't watch charts 24/7
- Emotional trading leads to losses
- No time to learn complex strategies

**How We Help**:
- Autonomous 24/7 trading
- Emotionless decision-making
- Proven EMA strategy built-in

### Persona 2: Crypto Developer

**Name**: Sarah  
**Age**: 32  
**Capital**: $1,000  
**Goal**: Build and customize trading bots  
**Pain Points**:
- Existing bots are black boxes
- Hard to extend functionality
- No learning/adaptation

**How We Help**:
- Open-source code
- Agent-based architecture (easy to extend)
- KB integration for continuous learning

---

## 🛠️ Technical Requirements

### Technology Stack

**Core**:
- **Language**: TypeScript (Node.js v22+)
- **Runtime**: Node.js
- **Package Manager**: npm/tsx

**Trading**:
- **Exchange**: Lighter DEX (Testnet → Mainnet)
- **API**: Lighter REST API + WebSocket
- **Indicators**: EMA (20/50/100/200), RSI, Volume

**Infrastructure**:
- **Database**: PostgreSQL (via KB)
- **Message Bus**: In-memory (future: Redis)
- **Notifications**: Telegram Bot API
- **Monitoring**: Custom dashboard + logs

**Development**:
- **Testing**: Jest (unit + integration)
- **CI/CD**: GitHub Actions
- **Deployment**: Docker + docker-compose
- **Version Control**: Git

### System Requirements

**Minimum**:
- CPU: 2 cores
- RAM: 2GB
- Storage: 10GB
- Network: Stable internet (< 100ms latency to exchange)

**Recommended**:
- CPU: 4 cores
- RAM: 4GB
- Storage: 50GB (for historical data)
- Network: Low-latency VPS (< 50ms)

### API Integrations

**Lighter DEX API** (`src/lib/lighter-api.ts`):
```typescript
interface LighterAPI {
  // Market Data
  getCandles(symbol: string, interval: string, limit: number): Promise<Candle[]>
  getTicker(symbol: string): Promise<Ticker>
  getOrderbook(symbol: string): Promise<Orderbook>
  
  // Trading
  createOrder(params: OrderParams): Promise<Order>
  cancelOrder(orderId: string): Promise<void>
  getOpenOrders(): Promise<Order[]>
  
  // Account
  getBalance(): Promise<Balance>
  getPositions(): Promise<Position[]>
}
```

**Telegram API** (notifications):
```typescript
interface TelegramNotifier {
  sendTradeAlert(trade: Trade): Promise<void>
  sendDailyReport(stats: DailyStats): Promise<void>
  sendError(error: Error): Promise<void>
}
```

---

## 🤖 Agent Architecture

### 5-Agent Team System

```
┌──────────────────────────────────────────────────────────┐
│                    Message Bus                           │
│         (Pub/Sub for inter-agent communication)          │
└──────────────────────────────────────────────────────────┘
                          ↓
    ┌─────────────────────┼─────────────────────┐
    │                     │                     │
    ↓                     ↓                     ↓
┌─────────┐         ┌──────────┐        ┌──────────┐
│ Analyzer│         │ Signal   │        │  Risk    │
│ Agent   │────────>│ Generator│───────>│ Manager  │
│         │         │  Agent   │        │  Agent   │
└─────────┘         └──────────┘        └──────────┘
                                              │
                                              ↓
                                        ┌──────────┐
                                        │ Executor │
                                        │  Agent   │
                                        └─────┬────┘
                                              │
                                              ↓
                                        ┌──────────┐
                                        │ Tracker  │
                                        │  Agent   │
                                        └──────────┘
```

### Agent 1: MultiTimeframeAnalyzer

**Purpose**: Collect and analyze market data across 3 timeframes

**Responsibilities**:
- Fetch candles (1m/5m/15m) every 1 second
- Calculate EMA (20/50/100/200) for each timeframe
- Calculate RSI, volume indicators
- Publish indicators to Message Bus

**Inputs**:
- Exchange API (candle data)

**Outputs**:
```typescript
interface MultiTimeframeIndicators {
  timestamp: number
  symbol: string
  currentPrice: number
  
  timeframes: {
    '1m': {
      ema20: number, ema50: number, ema100: number, ema200: number
      rsi: number, volume: number, change: number
    },
    '5m': { ... },
    '15m': { ... }
  }
}
```

**Trigger**: Timer (1000ms interval)

**Technology**:
- Lighter API Client
- EMA calculation (exponential moving average)
- RSI calculation (Relative Strength Index)

---

### Agent 2: SignalGenerator

**Purpose**: Generate trading signals with confidence scores

**Responsibilities**:
- Listen to indicator updates
- Calculate confidence score (0-100)
- Generate BUY/SELL signals only if confidence ≥ 60%
- Determine entry, target, stop-loss prices

**Inputs**:
- MultiTimeframeIndicators (from Analyzer)

**Outputs**:
```typescript
interface Signal {
  action: 'BUY' | 'SELL'
  confidence: number        // 0-100
  entry: number            // Entry price
  target: number           // Target price
  stopLoss: number         // Stop-loss price
  timeframe: string        // Dominant timeframe ('15m')
  positionSize: number     // % of capital
  reason: string           // Human-readable explanation
  breakdown: {             // Confidence breakdown
    alignment: number,     // 35 points max
    trend: number,         // 25 points max
    momentum: number,      // 20 points max
    rsi: number,          // 10 points max
    rr: number            // 10 points max
  }
}
```

**Logic**:
1. Calculate confidence using `confidence-calculator.ts`
2. If confidence < 60: return null (no signal)
3. If confidence ≥ 60: generate signal
4. Adjust position size based on confidence:
   - 80%+: 30% of capital
   - 70-79%: 24% of capital
   - 60-69%: 20% of capital

**Trigger**: Event-driven (on indicator update)

---

### Agent 3: RiskManager

**Purpose**: Validate and adjust trading signals

**Responsibilities**:
- Validate signal against risk rules
- Apply Kelly Criterion for position sizing
- Check portfolio constraints (max open positions)
- Prevent over-leveraging

**Inputs**:
- Signal (from SignalGenerator)
- Current portfolio state

**Outputs**:
```typescript
interface ValidatedSignal extends Signal {
  approved: boolean
  adjustedSize?: number     // If original size too large
  reason?: string          // If rejected
  kelly: number            // Kelly Criterion %
}
```

**Risk Rules**:
1. **Max Position**: 30% of capital per trade
2. **Max Open Positions**: 3 concurrent trades
3. **Kelly Criterion**: 
   ```
   kelly = (winRate * avgWin - lossRate * avgLoss) / avgWin
   finalSize = min(signalSize, kelly * capital)
   ```
4. **Stop Loss Verification**: Must be ≤ 2% from entry

**Trigger**: Event-driven (on signal generation)

---

### Agent 4: OrderExecutor

**Purpose**: Execute validated trades on Lighter DEX

**Responsibilities**:
- Place market orders via Lighter API
- Monitor order status
- Handle order failures
- Send Telegram notifications

**Inputs**:
- ValidatedSignal (from RiskManager)

**Outputs**:
```typescript
interface ExecutedTrade extends ValidatedSignal {
  orderId: string
  executedPrice: number
  executedSize: number
  timestamp: number
  status: 'FILLED' | 'PARTIAL' | 'FAILED'
}
```

**Execution Flow**:
1. Receive validated signal
2. Calculate exact order quantity (USDC → ETH conversion)
3. Place market order
4. Wait for fill confirmation (max 3 seconds)
5. If failed: retry 2x, then alert
6. Send Telegram notification
7. Publish trade to Tracker

**Error Handling**:
- Insufficient balance → alert user
- API timeout → retry with exponential backoff
- Partial fill → accept if > 80% filled

**Trigger**: Event-driven (on validated signal)

---

### Agent 5: PerformanceTracker

**Purpose**: Record and analyze trading performance

**Responsibilities**:
- Save all trades to database
- Calculate daily/weekly/monthly stats
- Track confidence-based performance
- Generate insights for learning

**Inputs**:
- ExecutedTrade (from Executor)

**Outputs**:
```typescript
interface PerformanceStats {
  period: 'daily' | 'weekly' | 'monthly'
  
  trades: {
    total: number
    wins: number
    losses: number
    winRate: number
  }
  
  returns: {
    total: number        // % return
    avgWin: number       // Avg % per winning trade
    avgLoss: number      // Avg % per losing trade
    rrRatio: number      // Reward/Risk ratio
  }
  
  byConfidence: {
    '60-69': { trades, winRate, avgReturn },
    '70-79': { ... },
    '80-89': { ... },
    '90-100': { ... }
  }
  
  capital: {
    starting: number
    current: number
    peak: number
    drawdown: number     // % from peak
  }
}
```

**Database Schema** (PostgreSQL via KB):
```sql
CREATE TABLE trades (
  id SERIAL PRIMARY KEY,
  timestamp TIMESTAMP,
  symbol VARCHAR(20),
  action VARCHAR(4),      -- BUY/SELL
  confidence INTEGER,
  entry_price DECIMAL,
  exit_price DECIMAL,
  size DECIMAL,
  pnl DECIMAL,
  pnl_percent DECIMAL,
  status VARCHAR(20),     -- OPEN/CLOSED/STOPPED
  reason TEXT
);

CREATE TABLE performance_snapshots (
  id SERIAL PRIMARY KEY,
  timestamp TIMESTAMP,
  period VARCHAR(20),
  total_trades INTEGER,
  win_rate DECIMAL,
  total_return DECIMAL,
  capital DECIMAL,
  max_drawdown DECIMAL
);
```

**Learning Integration**:
- Save high-confidence winning patterns to KB
- Identify losing patterns and adjust thresholds
- Weekly review: "What confidence level performed best?"

**Trigger**: Event-driven (on trade close) + Timer (daily report)

---

## 🎨 Core Features

### Feature 1: Multi-Timeframe Confidence Scoring

**Description**: 100-point scoring system that validates signals across 3 timeframes

**Components**:

1. **Timeframe Alignment** (35 points max)
   ```
   - 15m EMA alignment: 12 points
   - 5m EMA alignment: 8 points
   - 1m EMA alignment: 5 points
   - EMA distance convergence: 10 points
   ```

2. **Trend Direction** (25 points max)
   ```
   - 15m: EMA 20>50>100>200 = 12 points
   - 5m: EMA 20>50>100>200 = 8 points
   - 1m: EMA 20>50 = 5 points
   ```

3. **Momentum Strength** (20 points max)
   ```
   - All timeframes same direction: 10 points
   - 15m change > 3%: 5 points
   - 5m change > 2%: 3 points
   - 1m change > 1%: 2 points
   ```

4. **RSI Alignment** (10 points max)
   ```
   - 15m RSI in ideal range: 5 points
   - 5m RSI neutral (40-60): 3 points
   - 1m RSI not extreme: 2 points
   ```

5. **Risk/Reward Ratio** (10 points max)
   ```
   - R/R ≥ 3.0: 10 points
   - R/R ≥ 2.5: 8 points
   - R/R ≥ 2.0: 7 points
   - R/R ≥ 1.5: 5 points
   ```

**User Value**: Only trade when all factors align = higher win rate

---

### Feature 2: Adaptive Position Sizing

**Description**: Dynamic position sizing based on signal confidence

**Formula**:
```typescript
basePosition = capital * 0.20  // 20% default

if (confidence >= 80) {
  position = basePosition * 1.5  // 30%
} else if (confidence >= 70) {
  position = basePosition * 1.2  // 24%
} else if (confidence >= 60) {
  position = basePosition * 1.0  // 20%
}

// Kelly Criterion adjustment
kellyPosition = capital * kelly
finalPosition = min(position, kellyPosition, capital * 0.30)
```

**User Value**: 
- High confidence → larger bets → maximize winners
- Low confidence → smaller bets → minimize losers

---

### Feature 3: Multi-Level Stop Loss

**Description**: Timeframe-specific stop-loss levels

**Logic**:
```typescript
// 15m EMA50 (long-term support)
stopLoss15m = ema15m.ema50

// 5m EMA20 (medium-term support)
stopLoss5m = ema5m.ema20

// 1m hard stop (max loss tolerance)
stopLoss1m = entry * 0.985  // -1.5%

// Use closest stop above minimum
finalStopLoss = max(stopLoss15m, stopLoss5m, stopLoss1m)
```

**User Value**: 
- Prevents small noise from stopping out
- Still protects against major reversals

---

### Feature 4: Real-Time Telegram Notifications

**Description**: Instant alerts for trades and performance

**Messages**:

1. **Trade Alert**:
   ```
   🤖 New Trade
   ━━━━━━━━━━━━━━━
   BUY ETH-USDC
   Entry: $3,515
   Target: $3,625 (+3.1%)
   Stop: $3,480 (-1.0%)
   Confidence: 92%
   Size: $300 (30%)
   
   Reason:
   ✅ Perfect 15m/5m/1m alignment
   ✅ RSI 45 (ideal)
   ✅ R/R 3.5
   ```

2. **Daily Report**:
   ```
   📊 Daily Performance
   ━━━━━━━━━━━━━━━
   Date: 2026-03-06
   
   Trades: 3
   Wins: 2 | Losses: 1
   Win Rate: 66.7%
   
   PnL: +$45 (+4.5%)
   Capital: $1,045
   
   Best Trade: +$30 (92% confidence)
   Worst Trade: -$15 (65% confidence)
   ```

**User Value**: Stay informed without watching charts

---

### Feature 5: Knowledge Base Learning

**Description**: Continuous learning from past trades

**What We Track**:
- Winning patterns (confidence breakdown)
- Losing patterns (where score failed)
- Optimal confidence thresholds
- Best timeframe combinations

**Example Learning**:
```
Pattern: "15m EMA perfect alignment + 5m RSI 45-55"
Trades: 12
Win Rate: 91.7%
Avg Return: +2.8%
→ Save to KB as "high-confidence pattern"

Pattern: "1m only signal, 15m mixed"
Trades: 8
Win Rate: 37.5%
Avg Return: -0.8%
→ Flag in KB as "low-confidence pattern"
```

**User Value**: Bot gets smarter over time

---

## 📈 Trading Strategy

### Strategy Overview

**Name**: Multi-Timeframe EMA Convergence  
**Type**: Trend-following with momentum confirmation  
**Timeframes**: 1m (entry), 5m (confirmation), 15m (trend)  
**Indicators**: EMA 20/50/100/200, RSI, Volume

### Entry Rules

**LONG Entry** (all must be true):

1. **15m Timeframe**:
   - ✅ EMA alignment: 20 > 50 > 100 > 200
   - ✅ Price above all EMAs
   - ✅ RSI: 30-50 (oversold recovery)
   - ✅ Volume increasing

2. **5m Timeframe**:
   - ✅ EMA alignment: 20 > 50 > 100 > 200
   - ✅ Price above all EMAs
   - ✅ RSI: 40-60 (neutral)

3. **1m Timeframe**:
   - ✅ EMA 20 > EMA 50 (minimum)
   - ✅ Price above EMA 20
   - ✅ RSI < 70 (not overbought)

4. **Confidence**:
   - ✅ Total score ≥ 60 points

**SHORT Entry** (inverse of LONG):
- All EMAs in descending order
- RSI ranges inverted
- Confidence ≥ 60

### Exit Rules

**Take Profit**:
```typescript
target = entry + (entry - stopLoss) * 2.5  // 2.5:1 R/R minimum
```

**Stop Loss**:
```typescript
stopLoss = max(
  ema15m.ema50,      // Long-term support
  ema5m.ema20,       // Medium-term support
  entry * 0.985      // Hard stop at -1.5%
)
```

**Trailing Stop** (optional, future feature):
- Once +1% in profit: move stop to breakeven
- Once +2% in profit: trail at -1%

### Position Management

**Max Concurrent Positions**: 3

**Portfolio Allocation**:
```
Total Capital: 100%
├─ Active Trades: up to 90% (3 × 30%)
└─ Reserved: 10% (emergency buffer)
```

**Position Sizing Examples**:

| Capital | Confidence | Position % | Position $ |
|---------|-----------|-----------|-----------|
| $1,000 | 92% | 30% | $300 |
| $1,000 | 75% | 24% | $240 |
| $1,000 | 62% | 20% | $200 |
| $1,000 | 55% | 0% | WAIT |

---

## 🛡️ Risk Management

### Risk Parameters

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| Max Risk/Trade | 1.5% | Protect capital |
| Max Position Size | 30% | Limit exposure |
| Max Open Positions | 3 | Diversification |
| Max Drawdown | 15% | Circuit breaker |
| Min R/R Ratio | 1.5 | Ensure positive expectancy |

### Kelly Criterion Implementation

**Formula**:
```typescript
winRate = historicalWins / totalTrades
avgWin = avgWinningReturn  // e.g., 2.5%
avgLoss = avgLosingReturn  // e.g., 1.0%

kelly = (winRate * avgWin - (1 - winRate) * avgLoss) / avgWin

// Apply fractional Kelly (0.5x for safety)
fractionalKelly = kelly * 0.5

// Position size
positionSize = capital * fractionalKelly
```

**Example**:
```
Win Rate: 80%
Avg Win: +2.5%
Avg Loss: -1.0%

kelly = (0.8 * 2.5 - 0.2 * 1.0) / 2.5
      = (2.0 - 0.2) / 2.5
      = 0.72 (72%)

fractionalKelly = 0.72 * 0.5 = 0.36 (36%)

→ Use 30% max (capped for safety)
```

### Circuit Breakers

**Daily Loss Limit**:
```typescript
if (dailyLoss >= capital * 0.05) {  // -5%
  stopTrading()
  notifyUser("Daily loss limit reached")
}
```

**Max Drawdown**:
```typescript
drawdown = (peakCapital - currentCapital) / peakCapital

if (drawdown >= 0.15) {  // -15%
  stopTrading()
  requireManualRestart()
  notifyUser("Max drawdown reached - manual review required")
}
```

**Consecutive Losses**:
```typescript
if (consecutiveLosses >= 3) {
  pauseTrading(hours: 2)
  notifyUser("3 losses in a row - cooling off")
}
```

---

## 📊 Success Metrics

### Primary KPIs

| Metric | Target | Measurement |
|--------|--------|-------------|
| **Win Rate** | 80%+ | wins / totalTrades |
| **Monthly Return** | 20-80% | (endCapital - startCapital) / startCapital |
| **R/R Ratio** | > 2.0 | avgWin / avgLoss |
| **Max Drawdown** | < 15% | max((peak - current) / peak) |
| **Sharpe Ratio** | > 2.0 | (avgReturn - riskFreeRate) / stdDevReturns |

### Secondary KPIs

| Metric | Target | Purpose |
|--------|--------|---------|
| Avg Trade Duration | 2-6 hours | Verify scalping strategy |
| Trades per Day | 2-5 | Ensure quality over quantity |
| Confidence Accuracy | 80%+ | Validate scoring system |
| Order Fill Rate | 95%+ | Execution reliability |
| Slippage | < 0.1% | Market impact control |

### Confidence Performance Tracking

**Goal**: Validate that higher confidence = higher win rate

| Confidence Range | Expected Win Rate | Min Trades for Validity |
|------------------|-------------------|-------------------------|
| 90-100% | 90%+ | 10 |
| 80-89% | 85%+ | 15 |
| 70-79% | 80%+ | 20 |
| 60-69% | 75%+ | 25 |

**Analysis**:
```typescript
// Weekly review
const confidencePerformance = await tracker.getConfidenceStats('week')

for (const range of ['60-69', '70-79', '80-89', '90-100']) {
  const stats = confidencePerformance[range]
  
  if (stats.winRate < expectedWinRate[range]) {
    alert(`Confidence range ${range} underperforming`)
    // Adjust confidence threshold or scoring weights
  }
}
```

### Monitoring Dashboard

**Real-Time Metrics** (update every 1s):
- Current open positions
- Current P&L
- Active signals (confidence scores)

**Daily Metrics** (reset at 00:00 UTC):
- Trades today
- Win rate today
- P&L today
- Best/worst trade

**Historical Metrics** (all-time):
- Total trades
- Cumulative return
- Peak capital
- Current drawdown
- Sharpe ratio

---

## 🗓️ Development Roadmap

### Phase 1: Foundation (Week 1)

**Days 1-2: Planning**
- [x] Review strategy documents
- [ ] Create PRD (this document)
- [ ] Define agent roles
- [ ] Set up project structure

**Days 3-4: Core Libraries**
- [ ] `confidence-calculator.ts` - 100-point scoring
- [ ] `risk-manager.ts` - Kelly Criterion + validation
- [ ] `logger.ts` - Structured logging
- [ ] Unit tests (coverage > 80%)

**Days 5-7: Agent Development**
- [ ] `MultiTimeframeAnalyzer.ts`
- [ ] `SignalGenerator.ts`
- [ ] `RiskManager.ts` (agent wrapper)
- [ ] `OrderExecutor.ts`
- [ ] `PerformanceTracker.ts`

**Deliverables**:
- ✅ All 5 agents functional
- ✅ Message Bus communication working
- ✅ Unit tests passing

---

### Phase 2: Testing & Validation (Week 2)

**Days 8-10: Backtesting**
- [ ] Historical data collection (3 months)
- [ ] Backtest framework
- [ ] Run simulations
- [ ] Validate Win Rate > 70%

**Days 11-12: Paper Trading**
- [ ] Connect to Lighter Testnet
- [ ] Run live (no real money)
- [ ] Monitor for 48 hours
- [ ] Fix bugs, tune parameters

**Days 13-14: Integration Testing**
- [ ] Test all agent interactions
- [ ] Stress test (1000 signals)
- [ ] Failure recovery tests
- [ ] Performance optimization

**Deliverables**:
- ✅ Backtest results (Win Rate, Return, Drawdown)
- ✅ Paper trading validated (no critical bugs)
- ✅ Integration tests passing

---

### Phase 3: Deployment (Week 3)

**Days 15-16: Infrastructure**
- [ ] Dockerfile
- [ ] docker-compose.yml
- [ ] GitHub Actions CI/CD
- [ ] Monitoring dashboard

**Days 17-18: Testnet Launch**
- [ ] Deploy to Lighter Testnet
- [ ] Fund with testnet tokens
- [ ] Run for 5 days
- [ ] Validate metrics

**Days 19-21: Mainnet Preparation**
- [ ] Security audit
- [ ] Final testing
- [ ] User documentation
- [ ] Launch checklist

**Deliverables**:
- ✅ Dockerized application
- ✅ CI/CD pipeline active
- ✅ Testnet validation complete
- ✅ Ready for mainnet

---

### Phase 4: Mainnet & Iteration (Ongoing)

**Week 4+**:
- [ ] Mainnet launch (small capital)
- [ ] Monitor 24/7 (first week)
- [ ] Collect performance data
- [ ] Iterate based on results

**Future Features**:
- [ ] Multiple trading pairs (BTC-USDC, SOL-USDC)
- [ ] Trailing stop-loss
- [ ] Portfolio rebalancing
- [ ] Machine learning optimization
- [ ] Web UI dashboard
- [ ] Mobile app alerts

---

## 🔌 Dependencies & Integrations

### External Dependencies

**NPM Packages**:
```json
{
  "dependencies": {
    "axios": "^1.6.0",           // HTTP client
    "ws": "^8.14.0",             // WebSocket
    "pg": "^8.11.0",             // PostgreSQL
    "node-telegram-bot-api": "^0.64.0",  // Telegram
    "dotenv": "^16.3.0",         // Environment variables
    "winston": "^3.11.0"         // Logging
  },
  "devDependencies": {
    "typescript": "^5.3.0",
    "tsx": "^4.7.0",
    "@types/node": "^20.10.0",
    "jest": "^29.7.0",
    "@types/jest": "^29.5.0"
  }
}
```

### API Integrations

**Lighter DEX**:
- **Endpoint**: `https://api.lighter.xyz` (Testnet)
- **Auth**: API Key + Secret
- **Rate Limits**: 10 req/s (market data), 2 req/s (trading)
- **WebSocket**: Real-time candles + orderbook

**Telegram Bot**:
- **Bot Token**: Required
- **Chat ID**: User's Telegram ID
- **Features**: Send messages, buttons (future)

### Database Schema

**PostgreSQL Tables**:

```sql
-- Trades history
CREATE TABLE trades (
  id SERIAL PRIMARY KEY,
  timestamp TIMESTAMP NOT NULL,
  symbol VARCHAR(20) NOT NULL,
  action VARCHAR(4) NOT NULL,
  confidence INTEGER NOT NULL,
  entry_price DECIMAL(18, 8) NOT NULL,
  exit_price DECIMAL(18, 8),
  target_price DECIMAL(18, 8) NOT NULL,
  stop_loss DECIMAL(18, 8) NOT NULL,
  size_usdc DECIMAL(18, 2) NOT NULL,
  size_crypto DECIMAL(18, 8) NOT NULL,
  pnl_usdc DECIMAL(18, 2),
  pnl_percent DECIMAL(8, 4),
  status VARCHAR(20) NOT NULL,
  reason TEXT,
  created_at TIMESTAMP DEFAULT NOW(),
  closed_at TIMESTAMP
);

-- Performance snapshots
CREATE TABLE performance_snapshots (
  id SERIAL PRIMARY KEY,
  timestamp TIMESTAMP NOT NULL,
  period VARCHAR(20) NOT NULL,
  total_trades INTEGER NOT NULL,
  winning_trades INTEGER NOT NULL,
  losing_trades INTEGER NOT NULL,
  win_rate DECIMAL(5, 2) NOT NULL,
  total_return_percent DECIMAL(8, 4) NOT NULL,
  avg_win_percent DECIMAL(8, 4) NOT NULL,
  avg_loss_percent DECIMAL(8, 4) NOT NULL,
  rr_ratio DECIMAL(8, 4) NOT NULL,
  capital_usdc DECIMAL(18, 2) NOT NULL,
  peak_capital_usdc DECIMAL(18, 2) NOT NULL,
  max_drawdown_percent DECIMAL(5, 2) NOT NULL,
  created_at TIMESTAMP DEFAULT NOW()
);

-- Agent state (for recovery)
CREATE TABLE agent_state (
  agent_name VARCHAR(50) PRIMARY KEY,
  state JSONB NOT NULL,
  updated_at TIMESTAMP DEFAULT NOW()
);

-- Confidence patterns (learning)
CREATE TABLE confidence_patterns (
  id SERIAL PRIMARY KEY,
  pattern_name VARCHAR(100) NOT NULL,
  confidence_range VARCHAR(20) NOT NULL,
  total_trades INTEGER NOT NULL,
  win_rate DECIMAL(5, 2) NOT NULL,
  avg_return DECIMAL(8, 4) NOT NULL,
  last_updated TIMESTAMP DEFAULT NOW()
);
```

### Environment Variables

**Required** (`.env` file):
```bash
# Exchange
LIGHTER_API_KEY=your_api_key
LIGHTER_API_SECRET=your_secret
LIGHTER_TESTNET=true

# Trading
INITIAL_CAPITAL_USDC=1000
TRADING_SYMBOL=ETH-USDC

# Risk
MAX_POSITION_PERCENT=30
MAX_DRAWDOWN_PERCENT=15
MIN_CONFIDENCE=60

# Database
DATABASE_URL=postgresql://user:pass@localhost:5432/trading_bot

# Notifications
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id

# Monitoring
LOG_LEVEL=info
ENABLE_TELEGRAM_ALERTS=true
```

---

## 🎯 Acceptance Criteria

### MVP Definition

**Must Have**:
- [x] 5 agents running autonomously
- [x] Multi-timeframe analysis (1m/5m/15m)
- [x] Confidence scoring (100-point)
- [x] Order execution on Lighter Testnet
- [x] Performance tracking (database)
- [x] Telegram notifications
- [x] Win Rate > 70% (backtested)

**Should Have**:
- [ ] Kelly Criterion position sizing
- [ ] Consecutive loss circuit breaker
- [ ] Detailed Telegram reports
- [ ] Confidence pattern learning

**Could Have** (future):
- [ ] Multiple trading pairs
- [ ] Web dashboard
- [ ] Machine learning optimization

### Launch Checklist

**Pre-Launch**:
- [ ] All tests passing (unit + integration)
- [ ] Backtest validated (70%+ win rate)
- [ ] Paper trading successful (2 weeks)
- [ ] Testnet trading successful (5 days)
- [ ] Security review completed
- [ ] Documentation complete

**Launch Day**:
- [ ] Deploy to production server
- [ ] Fund account ($100 initial)
- [ ] Start agents
- [ ] Verify first signal + trade
- [ ] Monitor for 4 hours

**Post-Launch** (Week 1):
- [ ] Daily performance review
- [ ] No critical bugs
- [ ] Positive returns or < 5% drawdown
- [ ] User satisfaction (if beta testers)

---

## 📚 Appendix

### Glossary

- **EMA**: Exponential Moving Average - trend indicator
- **RSI**: Relative Strength Index - momentum oscillator
- **R/R Ratio**: Reward-to-Risk ratio (target / stop-loss)
- **Kelly Criterion**: Position sizing formula based on win rate
- **Drawdown**: Peak-to-trough decline in capital
- **Sharpe Ratio**: Risk-adjusted return metric
- **Slippage**: Difference between expected and actual fill price

### References

- [IMPROVED_STRATEGY.md](./IMPROVED_STRATEGY.md) - Detailed strategy
- [AGENT_TEAM_PLAN.md](./AGENT_TEAM_PLAN.md) - Agent team structure
- Lighter DEX Docs: https://docs.lighter.xyz
- EMA Strategy: Investopedia
- Kelly Criterion: https://en.wikipedia.org/wiki/Kelly_criterion

### Contact

**Project Lead**: ProductLead Agent  
**Technical Lead**: ArchitectAgent  
**Repository**: `/root/.openclaw/workspace/projects/crypto-trading-agents`

---

**Document Status**: ✅ Ready for Review  
**Next Step**: Architecture Design (ArchitectAgent)  
**Estimated Implementation**: 3 weeks to MVP

---

_This PRD is a living document. Update as requirements evolve._
