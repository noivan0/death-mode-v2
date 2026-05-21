#!/usr/bin/env python3
"""
10페어 전체 Binance Vision S3 최대 수집
- 5m: 최초 상장일부터 현재까지
- 15m/1h/4h/1d: 최초부터 현재
- freqtrade hyperliquid 폴더에 저장
"""
import requests, io, zipfile, json, datetime, time, sys
from pathlib import Path
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

DATADIR = Path("/root/.hermes/projects/death-mode-v2/user_data/data/hyperliquid/futures")
DATADIR.mkdir(parents=True, exist_ok=True)

BASE_URL = "https://data.binance.vision/data/futures/um/monthly/klines"

# Binance symbol → freqtrade pair (USDC 기준)
PAIRS = {
    "BTCUSDT":  "BTC/USDC:USDC",
    "ETHUSDT":  "ETH/USDC:USDC",
    "HYPEUSDT": "HYPE/USDC:USDC",
    "ZECUSDT":  "ZEC/USDC:USDC",
    "SOLUSDT":  "SOL/USDC:USDC",
    "LITUSDT":  "LIT/USDC:USDC",
    "TONUSDT":  "TON/USDC:USDC",
    "XRPUSDT":  "XRP/USDC:USDC",
    "VVVUSDT":  "VVV/USDC:USDC",
    "SUIUSDT":  "SUI/USDC:USDC",
}

# 각 페어 최초 데이터 기간 (확인됨)
PAIR_START = {
    "BTCUSDT":  (2020, 1),
    "ETHUSDT":  (2020, 1),
    "HYPEUSDT": (2025, 5),
    "ZECUSDT":  (2020, 2),
    "SOLUSDT":  (2020, 9),
    "LITUSDT":  (2021, 2),
    "TONUSDT":  (2024, 3),
    "XRPUSDT":  (2020, 1),
    "VVVUSDT":  (2025, 1),
    "SUIUSDT":  (2023, 5),
}

TIMEFRAMES = ["5m", "15m", "1h", "4h", "1d"]


def dl_month(sym, tf, yr, mo):
    url = f"{BASE_URL}/{sym}/{tf}/{sym}-{tf}-{yr}-{mo:02d}.zip"
    try:
        r = requests.get(url, timeout=60, verify=False)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        z = zipfile.ZipFile(io.BytesIO(r.content))
        lines = z.read(z.namelist()[0]).decode().strip().split('\n')
        candles = []
        for line in lines[1:]:
            row = line.split(',')
            if len(row) < 6:
                continue
            try:
                candles.append([int(row[0]), float(row[1]), float(row[2]),
                                 float(row[3]), float(row[4]), float(row[5])])
            except ValueError:
                continue
        return candles
    except Exception as e:
        print(f"    ERR {url}: {e}")
        return None


def save(ft_pair, tf, candles):
    safe = ft_pair.replace("/", "_").replace(":", "_")
    path = DATADIR / f"{safe}-{tf}-futures.json"
    existing = json.loads(path.read_text()) if path.exists() else []
    seen = {c[0]: c for c in existing}
    for c in candles:
        seen[c[0]] = c
    merged = sorted(seen.values(), key=lambda x: x[0])
    path.write_text(json.dumps(merged))
    return len(merged)


def month_range(start_y, start_m, end_y, end_m):
    y, m = start_y, start_m
    while (y, m) <= (end_y, end_m):
        yield y, m
        m += 1
        if m > 13:
            m = 1
            y += 1


now = datetime.datetime.utcnow()
end_y, end_m = now.year, now.month

target_syms = sys.argv[1:] if len(sys.argv) > 1 else list(PAIRS.keys())

for sym in target_syms:
    ft_pair = PAIRS[sym]
    start_y, start_m = PAIR_START[sym]
    print(f"\n{'='*55}")
    print(f"▶ {sym} ({ft_pair})  {start_y}-{start_m:02d} ~ {end_y}-{end_m:02d}")

    for tf in TIMEFRAMES:
        all_candles = []
        for yr, mo in month_range(start_y, start_m, end_y, end_m):
            c = dl_month(sym, tf, yr, mo)
            if c:
                all_candles.extend(c)
                print(f"  ✅ {tf} {yr}-{mo:02d} {len(c):,}캔들", flush=True)
            time.sleep(0.15)

        if all_candles:
            n = save(ft_pair, tf, all_candles)
            first = datetime.datetime.fromtimestamp(all_candles[0][0]/1000).strftime('%Y-%m-%d')
            last  = datetime.datetime.fromtimestamp(all_candles[-1][0]/1000).strftime('%Y-%m-%d')
            print(f"  📦 {tf}: {n:,}캔들 ({first}~{last}) 저장완료")
        else:
            print(f"  ⚠️  {tf}: 데이터 없음")

print("\n\n🎉 전체 수집 완료")
