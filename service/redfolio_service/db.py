from __future__ import annotations

import sqlite3
from pathlib import Path

INSTRUMENTS_TABLE_SQL = """
CREATE TABLE instruments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  code TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL DEFAULT '',
  security_type TEXT NOT NULL CHECK (security_type IN ('STOCK', 'ETF', 'ETF_LINK')),
  exchange TEXT NOT NULL DEFAULT '',
  industry TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


def ensure_db_parent(db_path: str) -> None:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)


def connect(db_path: str) -> sqlite3.Connection:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    return connection


def init_db(connection: sqlite3.Connection) -> None:
    connection.executescript(
        INSTRUMENTS_TABLE_SQL.replace("CREATE TABLE instruments", "CREATE TABLE IF NOT EXISTS instruments")
        + """
        CREATE TABLE IF NOT EXISTS transactions (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          instrument_id INTEGER NOT NULL REFERENCES instruments(id) ON DELETE CASCADE,
          side TEXT NOT NULL CHECK (side IN ('BUY', 'SELL')),
          trade_date TEXT NOT NULL,
          quantity REAL NOT NULL CHECK (quantity > 0),
          price REAL NOT NULL CHECK (price >= 0),
          fees REAL NOT NULL DEFAULT 0 CHECK (fees >= 0),
          note TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS market_snapshots (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          instrument_id INTEGER NOT NULL REFERENCES instruments(id) ON DELETE CASCADE,
          price REAL NOT NULL CHECK (price >= 0),
          as_of TEXT NOT NULL,
          source TEXT NOT NULL DEFAULT '',
          payload_json TEXT NOT NULL DEFAULT '{}',
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          UNIQUE (instrument_id, as_of, source)
        );

        CREATE TABLE IF NOT EXISTS dividend_events (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          instrument_id INTEGER NOT NULL REFERENCES instruments(id) ON DELETE CASCADE,
          ex_date TEXT NOT NULL,
          pay_date TEXT,
          record_date TEXT,
          cash_per_share REAL NOT NULL CHECK (cash_per_share >= 0),
          source TEXT NOT NULL DEFAULT '',
          status TEXT NOT NULL DEFAULT 'announced',
          raw_json TEXT NOT NULL DEFAULT '{}',
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          UNIQUE (instrument_id, ex_date, cash_per_share, source)
        );

        CREATE TABLE IF NOT EXISTS refresh_jobs (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          finished_at TEXT,
          status TEXT NOT NULL DEFAULT 'running',
          message TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS settings (
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL
        );
        """
    )
    migrate_instruments_security_type_check(connection)
    connection.commit()


def migrate_instruments_security_type_check(connection: sqlite3.Connection) -> None:
    row = connection.execute(
        """
        SELECT sql
        FROM sqlite_master
        WHERE type = 'table' AND name = 'instruments'
        """
    ).fetchone()
    sql = row["sql"] if isinstance(row, sqlite3.Row) else row[0] if row else ""
    if not row or "ETF_LINK" in str(sql):
        return

    connection.commit()
    connection.execute("PRAGMA foreign_keys = OFF")
    try:
        connection.execute("BEGIN")
        connection.execute(INSTRUMENTS_TABLE_SQL.replace("instruments", "instruments_new", 1))
        connection.execute(
            """
            INSERT INTO instruments_new
            (id, code, name, security_type, exchange, industry, created_at, updated_at)
            SELECT id, code, name, security_type, exchange, industry, created_at, updated_at
            FROM instruments
            """
        )
        connection.execute("DROP TABLE instruments")
        connection.execute("ALTER TABLE instruments_new RENAME TO instruments")
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise
    finally:
        connection.execute("PRAGMA foreign_keys = ON")

    violations = connection.execute("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise sqlite3.IntegrityError(f"foreign key violations after instruments migration: {violations}")


def row_to_dict(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row is not None else None


def rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict]:
    return [dict(row) for row in rows]
