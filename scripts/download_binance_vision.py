#!/usr/bin/env python3
"""
Binance Vision S3 배치 데이터 수집기
- HMG에서도 data.binance.vision 접근 가능 (API키 불필요)
- BTCUSDT/ETHUSDT/SOLUSDT Futures 월별 CSV 다운로드
- freqtrade JSON 형식으로 변환 저장
- 목표: 1m=6개월, 5m=2년 이상
"""
import requests, io, zipfile, json, datetime, sys, time
from pathlib import Path
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# 저장 경로 (hyperliquid 폴더에 같이 저장 — exchange=hyperliquid지만 OHLCV 구조 동일)
DATADIR_HL = Path("/root/.hermes/projects/death-mode-v2/user_data/data/hyperliquid/futures")
DATADIR_BN = Path("/root/.hermes/projects/death-mode-v2/user_data/data/binance/futures")
DATADIR_HL.mkdir(parents=True, exist_ok=True)
DATADIR_BN.mkdir(parents=True, exist_ok=True)

BASE_URL = "https://data.binance.vision/data/futures/um/monthly/klines"

# Binance → freqtrade 페어 매핑
PAIR_MAP = {
    "BTCUSDT": "BTC/USDC:USDC",  # HL은 USDC지만 가격 행동은 동일
    "ETHUSDT": "ETH/USDC:USDC",
    "SOLUSDT": "SOL/USDC:USDC",
}

TF_MAP = {
    "1m": "1m",
    "5m": "5m",
    "15m": "15m",
    "1h": "1h",
    "4h": "4h",
    "1d": "1d",
}


def download_monthly(symbol, interval, year, month):
    """월별 ZIP 다운로드 → candle list 반환"""
    url = f"{BASE_URL}/{symbol}/{interval}/{symbol}-{interval}-{year}-{month:02d}.zip"
    try:
        r = requests.get(url, timeout=60, verify=False)
        if r.status_code == 404:
            return None, "404"
        if r.status_code != 200:
            return None, f"HTTP {r.status_code}"
        z = zipfile.ZipFile(io.BytesIO(r.content))
        csv_name = z.namelist()[0]
        lines = z.read(csv_name).decode().strip().split('\n')
        # 첫 줄 헤더 제거
        candles = []
        for line in lines[1:]:
            row = line.split(',')
            if len(row) < 6:
                continue
            try:
                candles.append([
                    int(row[0]),       # timestamp ms
                    float(row[1]),     # open
                    float(row[2]),     # high
                    float(row[3]),     # low
                    float(row[4]),     # close
                    float(row[5]),     # volume
                ])
            except ValueError:
                continue
        return candles, "ok"
    except Exception as e:
        return None, str(e)


def pair_to_filename(ft_pair, timeframe, datadir):
    safe = ft_pair.replace("/", "_").replace(":", "_")
    return datadir / f"{safe}-{timeframe}-futures.json"


def merge_and_save(existing_path, new_candles):
    """기존 데이터와 병합 (중복 제거, 정렬)"""
    existing = []
    if existing_path.exists():
        existing = json.loads(existing_path.read_text())
    all_candles = existing + new_candles
    seen = {}
    for c in all_candles:
        seen[c[0]] = c
    merged = sorted(seen.values(), key=lambda x: x[0])
    existing_path.write_text(json.dumps(merged))
    return len(merged)


def download_range(symbol, interval, start_year, start_month, end_year, end_month,
                   ft_pair, save_both=True):
    """범위 내 모든 월 다운로드"""
    all_candles = []
    y, m = start_year, start_month
    while (y, m) <= (end_year, end_month):
        candles, status = download_monthly(symbol, interval, y, m)
        if status == "ok" and candles:
            all_candles.extend(candles)
            dt_str = f"{y}-{m:02d}"
            print(f"  ✅ {symbol} {interval} {dt_str}: {len(candles):,}캔들", flush=True)
        elif status == "404":
            print(f"  ⏭  {symbol} {interval} {y}-{m:02d}: 없음", flush=True)
        else:
            print(f"  ❌ {symbol} {interval} {y}-{m:02d}: {status}", flush=True)
        m += 1
        if m > 12:
            m = 1
            y += 1
        time.sleep(0.2)

    if not all_candles:
        return 0

    # Binance 폴더에 BTCUSDT 기준 저장
    bn_fname = DATADIR_BN / f"{symbol}-{interval}-futures.json"
    # freqtrade 형식 (HL pair명)은 HL 폴더에 저장
    hl_fname = pair_to_filename(ft_pair, interval, DATADIR_HL)

    n = merge_and_save(hl_fname, all_candles)
    merge_and_save(bn_fname, all_candles)

    if all_candles:
        first = datetime.datetime.fromtimestamp(all_candles[0][0]/1000).strftime('%Y-%m-%d')
        last  = datetime.datetime.fromtimestamp(all_candles[-1][0]/1000).strftime('%Y-%m-%d')
        print(f"\n  📦 {ft_pair} {interval}: 총 {n:,}캔들 ({first} ~ {last})", flush=True)
    return n


if __name__ == "__main__":
    now = datetime.datetime.utcnow()
    cur_year, cur_month = now.year, now.month

    # CLI: python download_binance_vision.py 1m 5m ...
    target_tfs = sys.argv[1:] if len(sys.argv) > 1 else ["1m", "5m", "15m", "1h", "4h", "1d"]

    # TF별 수집 기간 정의
    TF_MONTHS = {
        "1m":  6,   # 6개월
        "5m":  24,  # 2년
        "15m": 36,  # 3년
        "1h":  60,  # 5년
        "4h":  60,
        "1d":  60,
    }

    symbols = [("BTCUSDT", "BTC/USDC:USDC"),
               ("ETHUSDT", "ETH/USDC:USDC"),
               ("SOLUSDT", "SOL/USDC:USDC")]

    total = 0
    for tf in target_tfs:
        months_back = TF_MONTHS.get(tf, 12)
        # 시작 연월 계산
        start_ts = cur_month - months_back
        start_y = cur_year + (start_ts - 1) // 12
        start_m = ((start_ts - 1) % 12) + 1
        # 음수 처리
        while start_m <= 0:
            start_m += 12
            start_y -= 1

        print(f"\n{'='*55}")
        print(f"📊 {tf} 수집: {start_y}-{start_m:02d} ~ {cur_year}-{cur_month:02d} ({months_back}개월)")
        for symbol, ft_pair in symbols:
            n = download_range(symbol, tf, start_y, start_m, cur_year, cur_month, ft_pair)
            total += n

    print(f"\n\n🎉 전체 완료: 총 {total:,}캔들 수집")
    print(f"저장 위치: {DATADIR_HL}")
