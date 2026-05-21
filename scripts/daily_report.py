#!/usr/bin/env python3
"""DEATH MODE v2 — Daily P&L Report Generator.

Reads Freqtrade trade DB and generates a markdown summary.
Usage: python scripts/daily_report.py [--db-path user_data/tradesv3_hl_paper_nfi.sqlite]
"""
import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def get_trade_stats(db_path: str, days: int = 1) -> dict:
    if not os.path.exists(db_path):
        return {"error": f"DB not found: {db_path}"}
    
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    
    since = datetime.now(timezone.utc) - timedelta(days=days)
    
    cur.execute("""
        SELECT COUNT(*), 
               SUM(CASE WHEN profit_ratio > 0 THEN 1 ELSE 0 END),
               SUM(CASE WHEN profit_ratio <= 0 THEN 1 ELSE 0 END),
               COALESCE(SUM(profit_ratio), 0),
               COALESCE(AVG(profit_ratio), 0),
               COALESCE(MIN(profit_ratio), 0),
               COALESCE(MAX(profit_ratio), 0)
        FROM trades
        WHERE close_date > ?
    """, (since.isoformat(),))
    
    total, wins, losses, total_profit, avg_profit, worst, best = cur.fetchone()
    conn.close()
    
    total = total or 0
    return {
        "period_days": days,
        "total_trades": total,
        "wins": wins or 0,
        "losses": losses or 0,
        "winrate": f"{(wins/total*100):.1f}%" if total else "N/A",
        "total_profit_pct": f"{((total_profit or 0)*100):.2f}%",
        "avg_profit_pct": f"{((avg_profit or 0)*100):.2f}%",
        "best_trade_pct": f"{((best or 0)*100):.2f}%",
        "worst_trade_pct": f"{((worst or 0)*100):.2f}%",
    }

def main():
    import sys
    db_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        PROJECT_DIR, "user_data", "tradesv3_hl_paper_nfi.sqlite"
    )
    
    stats = get_trade_stats(db_path, days=1)
    stats["date"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    
    print("## 💀 DEATH MODE v2 — Daily Report")
    print(f"**{stats['date']}**\n")
    
    if "error" in stats:
        print(f"⚠️ {stats['error']}")
        return
    
    print(f"| Metric | Value |")
    print(f"|--------|-------|")
    print(f"| Period | {stats['period_days']}d |")
    print(f"| Total Trades | {stats['total_trades']} |")
    print(f"| Wins / Losses | {stats['wins']} / {stats['losses']} |")
    print(f"| Win Rate | {stats['winrate']} |")
    print(f"| Total P&L | {stats['total_profit_pct']} |")
    print(f"| Avg Profit | {stats['avg_profit_pct']} |")
    print(f"| Best Trade | {stats['best_trade_pct']} |")
    print(f"| Worst Trade | {stats['worst_trade_pct']} |")

if __name__ == "__main__":
    main()
