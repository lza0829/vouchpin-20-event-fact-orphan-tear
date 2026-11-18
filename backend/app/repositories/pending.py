from __future__ import annotations
import sqlite3
from typing import Any

PENDING_COLUMNS = (
    "id", "event_id", "coupon_code", "campaign_code", "amount",
    "business_day", "attempts", "last_error", "created_at", "updated_at",
)


def insert_pending(conn: sqlite3.Connection, **fields: Any) -> int:
    cur = conn.execute(
        "INSERT INTO pending_facts(event_id, coupon_code, campaign_code, amount, business_day) "
        "VALUES(?,?,?,?,?)",
        (
            int(fields["event_id"]),
            fields["coupon_code"],
            fields["campaign_code"],
            float(fields["amount"]),
            fields["business_day"],
        ),
    )
    return int(cur.lastrowid)


def list_pending(conn: sqlite3.Connection, business_day: str | None = None) -> list[dict[str, Any]]:
    if business_day:
        rows = conn.execute(
            "SELECT * FROM pending_facts WHERE business_day=? ORDER BY id",
            (business_day,),
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM pending_facts ORDER BY id").fetchall()
    return [dict(r) for r in rows]


def pending_count(conn: sqlite3.Connection, business_day: str | None = None) -> int:
    if business_day:
        row = conn.execute(
            "SELECT COUNT(*) c FROM pending_facts WHERE business_day=?",
            (business_day,),
        ).fetchone()
    else:
        row = conn.execute("SELECT COUNT(*) c FROM pending_facts").fetchone()
    return int(row["c"])


def mark_attempt(conn: sqlite3.Connection, pending_id: int, error: str) -> None:
    conn.execute(
        "UPDATE pending_facts SET attempts=attempts+1, last_error=?, updated_at=datetime('now') WHERE id=?",
        (error, pending_id),
    )


def delete_pending(conn: sqlite3.Connection, pending_id: int) -> None:
    conn.execute("DELETE FROM pending_facts WHERE id=?", (pending_id,))
