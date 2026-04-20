# db.py
import sqlite3
import os
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trade_history.db")


def get_conn():
    return sqlite3.connect(DB_PATH)


def init_db():
    """테이블 초기화"""
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                dt          TEXT NOT NULL,
                stock_code  TEXT NOT NULL,
                stock_name  TEXT,
                order_type  TEXT NOT NULL,
                quantity    INTEGER NOT NULL,
                price       INTEGER NOT NULL,
                avg_price   INTEGER DEFAULT 0,
                pnl         INTEGER DEFAULT 0,
                pnl_rate    REAL DEFAULT 0.0,
                account_no  TEXT DEFAULT '',
                status      TEXT DEFAULT 'done'
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        conn.commit()


def insert_trade(stock_code, stock_name, order_type, quantity, price,
                 avg_price=0, pnl=0, pnl_rate=0.0, account_no=""):
    dt = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO trades
              (dt, stock_code, stock_name, order_type, quantity, price,
               avg_price, pnl, pnl_rate, account_no)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (dt, stock_code, stock_name, order_type, quantity, price,
              avg_price, pnl, pnl_rate, account_no))
        conn.commit()


def get_trades(period="day", account_no=""):
    """기간별 거래내역 조회"""
    from datetime import timedelta
    now = datetime.now()
    if period == "day":
        since = now.strftime("%Y-%m-%d")
    elif period == "week":
        since = (now - timedelta(days=7)).strftime("%Y-%m-%d")
    elif period == "month":
        since = now.strftime("%Y-%m-01")
    elif period == "year":
        since = now.strftime("%Y-01-01")
    else:
        since = "2000-01-01"

    with get_conn() as conn:
        conn.row_factory = sqlite3.Row
        if account_no:
            cur = conn.execute("""
                SELECT * FROM trades
                WHERE dt >= ? AND account_no = ?
                ORDER BY dt DESC
            """, (since, account_no))
        else:
            cur = conn.execute("""
                SELECT * FROM trades
                WHERE dt >= ?
                ORDER BY dt DESC
            """, (since,))
        return [dict(r) for r in cur.fetchall()]


def get_pnl_summary(period="day", account_no=""):
    """기간별 손익 합계"""
    trades = get_trades(period, account_no)
    sells  = [t for t in trades if t["order_type"] == "매도"]
    total_pnl = sum(t["pnl"] for t in sells)
    win = sum(1 for t in sells if t["pnl"] > 0)
    return {
        "total_pnl":    total_pnl,
        "total_trades": len(trades),
        "win_count":    win,
        "lose_count":   len(sells) - win,
        "win_rate":     round(win / len(sells) * 100, 1) if sells else 0.0,
    }


def save_setting(key, value):
    with get_conn() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)
        """, (key, str(value)))
        conn.commit()


def load_setting(key, default=None):
    with get_conn() as conn:
        cur = conn.execute("SELECT value FROM settings WHERE key=?", (key,))
        row = cur.fetchone()
        return row[0] if row else default
