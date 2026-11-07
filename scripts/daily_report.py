#!/usr/bin/env python3
"""DEATH MODE v2 — Daily P&L Report Generator.

Reads Freqtrade trade DB + Cardinal journal and generates a markdown summary.
Usage:
  python scripts/daily_report.py
  python scripts/daily_report.py user_data/tradesv3_hl_paper_nfi.sqlite
"""
from __future__ import annotations

import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_DIR = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, str(PROJECT_DIR))


def get_trade_stats(db_path: str, days: int = 1) -> dict:
    if not os.path.exists(db_path):
        return {"error": f"DB not found: {db_path}"}

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    since = datetime.now(timezone.utc) - timedelta(days=days)

    cur.execute(
        """
        SELECT COUNT(*),
               SUM(CASE WHEN profit_ratio > 0 THEN 1 ELSE 0 END),
               SUM(CASE WHEN profit_ratio <= 0 THEN 1 ELSE 0 END),
               COALESCE(SUM(profit_ratio), 0),
               COALESCE(AVG(profit_ratio), 0),
               COALESCE(MIN(profit_ratio), 0),
               COALESCE(MAX(profit_ratio), 0),
               COALESCE(SUM(close_profit_abs), 0),
               COALESCE(AVG(CASE WHEN profit_ratio > 0 THEN profit_ratio END), 0),
               COALESCE(AVG(CASE WHEN profit_ratio <= 0 THEN profit_ratio END), 0)
        FROM trades
        WHERE close_date > ?
        """,
        (since.isoformat(),),
    )
    (
        total, wins, losses, total_profit, avg_profit, worst, best,
        abs_pnl, avg_win, avg_loss,
    ) = cur.fetchone()
    conn.close()

    total = total or 0
    wins = wins or 0
    losses = losses or 0
    avg_win = avg_win or 0
    avg_loss = avg_loss or 0
    # Expectancy in R terms approximating |avg_loss| as 1R
    expectancy_r = None
    if total and avg_loss < 0:
        winrate = wins / total
        payoff = abs(avg_win / avg_loss) if avg_loss != 0 else 0
        expectancy_r = winrate * payoff - (1 - winrate)

    return {
        "period_days": days,
        "total_trades": total,
        "wins": wins,
        "losses": losses,
        "winrate": f"{(wins / total * 100):.1f}%" if total else "N/A",
        "total_profit_pct": f"{((total_profit or 0) * 100):.2f}%",
        "abs_pnl": round(abs_pnl or 0, 4),
        "avg_profit_pct": f"{((avg_profit or 0) * 100):.2f}%",
        "avg_win_pct": f"{(avg_win * 100):.2f}%",
        "avg_loss_pct": f"{(avg_loss * 100):.2f}%",
        "payoff_ratio": round(abs(avg_win / avg_loss), 2) if avg_loss else None,
        "expectancy_r": round(expectancy_r, 3) if expectancy_r is not None else None,
        "best_trade_pct": f"{((best or 0) * 100):.2f}%",
        "worst_trade_pct": f"{((worst or 0) * 100):.2f}%",
    }


def find_dbs() -> list[Path]:
    ud = PROJECT_DIR / "user_data"
    return sorted(ud.glob("tradesv3*.sqlite"))


def main() -> None:
    db_path = sys.argv[1] if len(sys.argv) > 1 else None
    dbs = [Path(db_path)] if db_path else find_dbs()
    if not dbs:
        dbs = [PROJECT_DIR / "user_data" / "tradesv3_hl_paper_nfi.sqlite"]

    print("## 💀 DEATH MODE v2 — Daily Report")
    print(f"**{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}**\n")

    for db in dbs:
        stats = get_trade_stats(str(db), days=1)
        print(f"### DB: `{db.name}`\n")
        if "error" in stats:
            print(f"⚠️ {stats['error']}\n")
            continue
        print("| Metric | Value |")
        print("|--------|-------|")
        print(f"| Period | {stats['period_days']}d |")
        print(f"| Total Trades | {stats['total_trades']} |")
        print(f"| Wins / Losses | {stats['wins']} / {stats['losses']} |")
        print(f"| Win Rate | {stats['winrate']} |")
        print(f"| Total P&L % | {stats['total_profit_pct']} |")
        print(f"| Abs P&L | {stats['abs_pnl']} |")
        print(f"| Avg Win / Loss | {stats['avg_win_pct']} / {stats['avg_loss_pct']} |")
        print(f"| Payoff (W/L) | {stats['payoff_ratio']} |")
        print(f"| Expectancy (R) | {stats['expectancy_r']} |")
        print(f"| Best / Worst | {stats['best_trade_pct']} / {stats['worst_trade_pct']} |")
        print()

    try:
        from core.risk import DecisionJournal, RiskGate, TiltGuard
        gate = RiskGate()
        tilt = TiltGuard()
        journal = DecisionJournal().summarize()
        print("### Cardinal Cage\n")
        print("| Metric | Value |")
        print("|--------|-------|")
        print(f"| Kill switch | {'ON' if gate.killed else 'OFF'} |")
        print(f"| Day PnL | {gate.day_pnl:.4f} |")
        print(f"| Tilt score | {tilt.tilt_score()}/100 |")
        print(f"| Journal allow/block/shrink | "
              f"{journal.get('allows')}/{journal.get('blocks')}/{journal.get('shrinks')} |")
        print(f"| Avg planned R:R | {journal.get('avg_planned_rr')} |")
        print()
        print("> Martin Luk reminder: low winrate is fine if payoff and expectancy stay positive.")
    except Exception as e:
        print(f"⚠️ Cardinal status unavailable: {e}")


if __name__ == "__main__":
    main()
