"""
SQLite storage layer for Flip Da' Coin RNG study.

Stores coin flip outcomes with timestamps for statistical analysis.
"""

import sqlite3
from pathlib import Path
from datetime import datetime, timezone

DB_PATH = Path(__file__).parent.parent / "data" / "flipdacoin.db"

OUTCOMES = ["HEADS", "TAILS", "MIDDLE"]


def get_connection(db_path: Path = DB_PATH) -> sqlite3.Connection:
    """Get a database connection with WAL mode."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """Create tables if they don't exist."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS flips (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            round_id    TEXT,
            timestamp   TEXT NOT NULL,
            outcome     TEXT NOT NULL CHECK(outcome IN ('HEADS', 'TAILS', 'MIDDLE')),
            scraped_at  TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS bets (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            round_id    TEXT,
            timestamp   TEXT NOT NULL,
            selection   TEXT NOT NULL CHECK(selection IN ('HEADS', 'TAILS')),
            stake       REAL NOT NULL,
            outcome     TEXT,
            won         INTEGER,
            payout      REAL DEFAULT 0,
            profit      REAL
        );

        CREATE INDEX IF NOT EXISTS idx_flips_outcome ON flips(outcome);
        CREATE INDEX IF NOT EXISTS idx_flips_timestamp ON flips(timestamp);
    """)
    conn.commit()


def insert_flip(conn: sqlite3.Connection, outcome: str,
                round_id: str = None, timestamp: str = None) -> int:
    """Insert a coin flip result. Returns the row id."""
    if timestamp is None:
        timestamp = datetime.now(timezone.utc).isoformat()
    scraped_at = datetime.now(timezone.utc).isoformat()

    cursor = conn.execute(
        "INSERT INTO flips (round_id, timestamp, outcome, scraped_at) VALUES (?, ?, ?, ?)",
        (round_id, timestamp, outcome, scraped_at),
    )
    conn.commit()
    return cursor.lastrowid


def get_stats(conn: sqlite3.Connection) -> dict:
    """Get overall flip statistics."""
    total = conn.execute("SELECT COUNT(*) FROM flips").fetchone()[0]
    if total == 0:
        return {'total': 0, 'heads': 0, 'tails': 0, 'middle': 0,
                'heads_pct': 0, 'tails_pct': 0, 'middle_pct': 0}

    heads = conn.execute("SELECT COUNT(*) FROM flips WHERE outcome='HEADS'").fetchone()[0]
    tails = conn.execute("SELECT COUNT(*) FROM flips WHERE outcome='TAILS'").fetchone()[0]
    middle = conn.execute("SELECT COUNT(*) FROM flips WHERE outcome='MIDDLE'").fetchone()[0]

    return {
        'total': total,
        'heads': heads, 'tails': tails, 'middle': middle,
        'heads_pct': heads / total * 100,
        'tails_pct': tails / total * 100,
        'middle_pct': middle / total * 100,
        'house_edge': middle / total * 100,
    }
