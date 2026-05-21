#!/usr/bin/env python3
"""
HL 상위 10페어 5m 2년치 Binance Vision 다운로드
BTC/ETH/SOL + XRP/DOGE/SUI/LINK/NEAR/AAVE/AVAX
"""
import requests, io, zipfile, json, datetime, time
from pathlib import Path
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

DATADIR = Path("/root/.hermes/projects/death-mode-v2/user_data/data/hyperliquid/futures")
DATADIR.mkdir(parents=True, exist_ok=True)
BASE = "https://data.binance.vision/data/futures/um/monthly/klines"

# Binance symbol → freqtrade HL pair명
PAIRS = {
    "BTCUSDT":  "BTC/USDC:USDC",
    "ETHUSDT":  "ETH/USDC:USDC",
    "SOLUSDT":  "SOL/USDC:USDC",
    "XRPUSDT":  "XRP/USDC:USDC",
    "DOGEUSDT": "DOGE/USDC:USDC",
    "SUIUSDT":  "SUI/USDC:USDC",
    "LINKUSDT": "LINK/USDC:USDC",
    "NEARUSDT": "NEAR/USDC:USDC",
    "AAVEUSDT": "AAVE/USDC:USDC",
    "AVAXUSDT": "AVAX/USDC:USDC",
}

def download_month(symbol, tf, year, month):
    url = f"{BASE}/{symbol}/{tf}/{symbol}-{tf}-{year}-{month:02d}.zip"
    r = requests.get(url, timeout=60, verify=False)
    if r.status_code == 404:
        return None
    z = zipfile.ZipFile(io.BytesIO(r.content))
    lines = z.read(z.namelist()[0]).decode().strip().split('\n')
    candles = []
    for line in lines[1:]:
        row = line.split(',')
        if len(row) < 6: continue
        try:
            candles.append([int(row[0]), float(row[1]), float(row[2]),
                            float(row[3]), float(row[4]), float(row[5])])
        except: continue
    return candles

def save_pair(ft_pair, tf, all_candles):
    safe = ft_pair.replace("/", "_").replace(":", "_")
    fname = DATADIR / f"{safe}-{tf}-futures.json"
    # 기존 데이터와 병합
    existing = json.loads(fname.read_text()) if fname.exists() else []
    seen = {c[0]: c for c in existing + all_candles}
    merged = sorted(seen.values(), key=lambda x: x[0])
    fname.write_text(json.dumps(merged))
    return len(merged), merged

if __name__ == "__main__":
    import datetime as dt
    now = dt.datetime.utcnow()

    # 5m 2년치: 2024-05 ~ 2026-04
    months = []
    y, m = 2024, 5
    while (y, m) <= (now.year, now.month):
        months.append((y, m))
        m += 1
        if m > 12: m = 1; y += 1

    print(f"수집 계획: {len(PAIRS)}페어 × {len(months)}개월 = {len(PAIRS)*len(months)}배치")
    print(f"기간: 2024-05 ~ {now.year}-{now.month:02d}\n")

    total_candles = 0
    for symbol, ft_pair in PAIRS.items():
        all_c = []
        print(f"\n{'='*50}")
        print(f"📊 {symbol} → {ft_pair}")
        for y, m in months:
            candles = download_month(symbol, "5m", y, m)
            if candles:
                all_c.extend(candles)
                print(f"  ✅ {y}-{m:02d}: {len(candles):,}캔들", flush=True)
            else:
                print(f"  ⏭  {y}-{m:02d}: 없음", flush=True)
            time.sleep(0.15)
        if all_c:
            n, merged = save_pair(ft_pair, "5m", all_c)
            first = dt.datetime.fromtimestamp(merged[0][0]/1000).strftime('%Y-%m-%d')
            last  = dt.datetime.fromtimestamp(merged[-1][0]/1000).strftime('%Y-%m-%d')
            print(f"  💾 저장: {n:,}캔들 ({first}~{last})")
            total_candles += n

    print(f"\n\n🎉 완료: 총 {total_candles:,}캔들")
