from pathlib import Path
import sqlite3


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "market.db"


def get_conn():
    return sqlite3.connect(str(DB_PATH))


def init_db():
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS prices (
            index_name TEXT,
            date TEXT,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume REAL,
            PRIMARY KEY (index_name, date)
        )
        """
    )
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS trade_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            generated_at TEXT NOT NULL,
            execution_mode TEXT NOT NULL,
            trading_enabled INTEGER NOT NULL DEFAULT 1,
            discovered_count INTEGER NOT NULL DEFAULT 0,
            eligible_count INTEGER NOT NULL DEFAULT 0,
            selected_count INTEGER NOT NULL DEFAULT 0,
            placed_count INTEGER NOT NULL DEFAULT 0,
            skipped_count INTEGER NOT NULL DEFAULT 0,
            failed_count INTEGER NOT NULL DEFAULT 0,
            total_risk_amount REAL NOT NULL DEFAULT 0,
            report_path TEXT,
            details_json TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS trade_signals (
            signal_uid TEXT PRIMARY KEY,
            strategy_id TEXT NOT NULL,
            strategy_title TEXT,
            ticker TEXT NOT NULL,
            symbol TEXT,
            market TEXT,
            trade_type TEXT,
            side TEXT NOT NULL,
            signal_time TEXT,
            entry_time TEXT,
            entry_price REAL,
            stop_price REAL,
            target_price REAL,
            rr_ratio REAL,
            vol_mult REAL,
            notify_key TEXT,
            source_path TEXT,
            source_generated_at TEXT,
            latest_status TEXT NOT NULL DEFAULT 'DISCOVERED',
            attempt_count INTEGER NOT NULL DEFAULT 0,
            next_retry_at TEXT,
            last_attempt_at TEXT,
            last_error TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS trade_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_uid TEXT NOT NULL,
            strategy_id TEXT NOT NULL,
            ticker TEXT NOT NULL,
            symbol TEXT,
            market TEXT,
            side TEXT NOT NULL,
            order_type TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            entry_price REAL,
            stop_price REAL,
            target_price REAL,
            risk_amount REAL,
            risk_per_unit REAL,
            notional REAL,
            execution_mode TEXT NOT NULL,
            broker_order_id TEXT,
            broker_payload_json TEXT,
            broker_response_json TEXT,
            status TEXT NOT NULL,
            error TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    c.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_trade_orders_signal_mode
        ON trade_orders(signal_uid, execution_mode)
        """
    )
    c.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_trade_orders_status
        ON trade_orders(status, updated_at)
        """
    )
    c.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_trade_signals_status
        ON trade_signals(latest_status, updated_at)
        """
    )
    conn.commit()
    conn.close()
