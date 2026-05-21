#!/usr/bin/env python3
"""
Hyperliquid 배치 데이터 수집기
- 1m: 최대 90일 (5000캔들/배치 = 3.5일씩)
- 5m: 최대 180일 (5000캔들/배치 = 17.4일씩)
- freqtrade JSON 형식으로 저장
"""
import json, time, datetime, requests, sys
from pathlib import Path

# HMG SSL 우회
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

import os
PROJ_ROOT = Path(os.environ.get("PROJ_DIR", Path(__file__).parent.parent))
DATADIR = PROJ_ROOT / "user_data" / "data" / "hyperliquid" / "futures"
DATADIR.mkdir(parents=True, exist_ok=True)

HL_API = "https://api.hyperliquid.xyz/info"

COIN_MAP = {
    "BTC/USDC:USDC": "BTC",
    "ETH/USDC:USDC": "ETH",
    "SOL/USDC:USDC": "SOL",
}

TF_SECS = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
}

MAX_CANDLES = 4900  # 안전 마진


def fetch_candles(coin, interval, start_ms, end_ms):
    body = {
        "type": "candleSnapshot",
        "req": {"coin": coin, "interval": interval, "startTime": start_ms, "endTime": end_ms}
    }
    try:
        r = requests.post(HL_API, json=body, timeout=30, verify=False)
        return r.json()
    except Exception as e:
        print(f"  오류: {e}")
        return []


def pair_to_filename(pair, timeframe):
    safe = pair.replace("/", "_").replace(":", "_")
    return DATADIR / f"{safe}-{timeframe}-futures.json"


def download_pair(pair, timeframe, days_back):
    coin = COIN_MAP[pair]
    tf_sec = TF_SECS[timeframe]
    batch_ms = MAX_CANDLES * tf_sec * 1000

    now_ms = int(time.time() * 1000)
    start_ms = now_ms - days_back * 86400 * 1000

    all_candles = []
    cursor = start_ms
    batch_count = 0

    print(f"  [{pair} {timeframe}] {days_back}일 배치 수집...", flush=True)
    while cursor < now_ms:
        batch_end = min(cursor + batch_ms, now_ms)
        candles = fetch_candles(coin, timeframe, cursor, batch_end)
        if candles:
            all_candles.extend(candles)
            batch_count += 1
            last_t = datetime.datetime.fromtimestamp(candles[-1]['t']/1000).strftime('%Y-%m-%d')
            print(f"    배치 {batch_count}: {len(candles)}캔들 (to {last_t})", flush=True)
        cursor = batch_end + 1000
        time.sleep(0.3)

    # 중복 제거 + 정렬
    seen = {}
    for c in all_candles:
        seen[c['t']] = c
    sorted_candles = sorted(seen.values(), key=lambda x: x['t'])

    # freqtrade 형식: [timestamp, open, high, low, close, volume]
    ft_candles = [
        [c['t'], float(c['o']), float(c['h']), float(c['l']), float(c['c']), float(c['v'])]
        for c in sorted_candles
    ]

    fname = pair_to_filename(pair, timeframe)
    with open(fname, 'w') as f:
        json.dump(ft_candles, f)

    if ft_candles:
        first = datetime.datetime.fromtimestamp(ft_candles[0][0]/1000).strftime('%Y-%m-%d')
        last = datetime.datetime.fromtimestamp(ft_candles[-1][0]/1000).strftime('%Y-%m-%d')
        actual_days = (ft_candles[-1][0] - ft_candles[0][0]) / (1000*86400)
        print(f"  ✅ {pair} {timeframe}: {len(ft_candles):,}캔들 ({first}~{last}, {actual_days:.1f}일)", flush=True)
    else:
        print(f"  ⚠️  {pair} {timeframe}: 데이터 없음", flush=True)
    return len(ft_candles)


if __name__ == "__main__":
    pairs = ["BTC/USDC:USDC", "ETH/USDC:USDC", "SOL/USDC:USDC"]

    # 수집 계획 (timeframe, days_back)
    plan = [
        ("1m",  90),   # 90일
        ("5m",  180),  # 180일
        ("15m", 365),  # 1년
        ("1h",  730),  # 2년
        ("4h",  1000), # ~3년
        ("1d",  1000), # ~3년
    ]

    # CLI 인자로 특정 TF만 수집 가능
    if len(sys.argv) > 1:
        tfs = sys.argv[1:]
        plan = [(tf, days) for tf, days in plan if tf in tfs]
        print(f"지정 타임프레임: {[tf for tf,_ in plan]}")

    total = 0
    for tf, days in plan:
        print(f"\n{'='*50}")
        print(f"📊 {tf} 데이터 수집 ({days}일)")
        for pair in pairs:
            n = download_pair(pair, tf, days)
            total += n

    print(f"\n\n✅ 전체 완료: 총 {total:,}캔들 수집")
