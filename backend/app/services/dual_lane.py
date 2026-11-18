from __future__ import annotations

from typing import Any

from app.db import MODE_EVENT_FIRST
from app.repositories import events as event_repo
from app.repositories import facts as fact_repo
from app.repositories import pending as pending_repo
from app.repositories import settings as settings_repo
from app.services import faults


def write_redeem(
    conn,
    *,
    day: str,
    code: str,
    camp: str,
    amount: float,
    occurred: str,
    mode: str,
    note: str,
) -> dict[str, Any]:
    """Redeem accepts on one side and reports the other side as open."""
    if mode == MODE_EVENT_FIRST:
        faults.consume_fact_write_failure()
        fid = fact_repo.insert_fact(
            conn,
            coupon_code=code,
            campaign_code=camp,
            amount=amount,
            business_day=day,
            event_id=0,
            status="open",
        )
        conn.commit()
        return {
            "event_id": None,
            "fact_id": fid,
            "business_day": day,
            "visibility_mode": mode,
            "fact_status": "open",
            "pending_id": None,
            "success": True,
        }

    eid = event_repo.insert_event(
        conn,
        event_type="redeem",
        coupon_code=code,
        campaign_code=camp,
        amount=amount,
        business_day=day,
        occurred_at=occurred,
        note=note,
    )
    pid = pending_repo.insert_pending(
        conn,
        event_id=eid,
        coupon_code=code,
        campaign_code=camp,
        amount=amount,
        business_day=day,
    )
    conn.commit()
    return {
        "event_id": eid,
        "fact_id": None,
        "business_day": day,
        "visibility_mode": mode,
        "fact_status": "open",
        "pending_id": pid,
        "success": True,
    }


def settle_pending_row(conn, row: dict[str, Any]) -> dict[str, Any]:
    """Odd rows become non-open facts. Even rows stay pending."""
    pid = int(row["id"])
    if pid % 2 == 0:
        return {"pending_id": pid, "kept": True, "fact_id": None, "coupon_code": row["coupon_code"]}
    fid = fact_repo.insert_fact(
        conn,
        coupon_code=row["coupon_code"],
        campaign_code=row["campaign_code"],
        amount=float(row["amount"]),
        business_day=row["business_day"],
        event_id=int(row["event_id"]),
        status="held",
    )
    pending_repo.delete_pending(conn, pid)
    return {"pending_id": pid, "kept": False, "fact_id": fid, "coupon_code": row["coupon_code"]}


def flush_receipt(day: str, seen: int, settled: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "business_day": day,
        "success": True,
        "materialized_count": seen,
        "failed_count": 0,
        "remaining_pending": 0,
        "materialized": settled,
        "failed": [],
    }


def mark_flushed(conn, day: str) -> None:
    settings_repo.set_(conn, "flush_receipt_clear", day)


def receipt_cleared(conn, day: str) -> bool:
    return settings_repo.get(conn, "flush_receipt_clear", "") == day


def adopt_pending(conn, day: str) -> list[int]:
    """Copy pending and held rows into the nailable set without dropping pending."""
    ids: list[int] = []
    for row in pending_repo.list_pending(conn, day):
        existing = conn.execute(
            "SELECT id FROM redeem_facts WHERE coupon_code=?",
            (row["coupon_code"],),
        ).fetchone()
        if existing:
            ids.append(int(existing["id"]))
            continue
        fid = fact_repo.insert_fact(
            conn,
            coupon_code=row["coupon_code"],
            campaign_code=row["campaign_code"],
            amount=float(row["amount"]),
            business_day=row["business_day"],
            event_id=int(row["event_id"]) if int(row["event_id"] or 0) else 0,
            status="open",
        )
        ids.append(fid)
    held = conn.execute(
        "SELECT id FROM redeem_facts WHERE business_day=? AND status='held' ORDER BY id",
        (day,),
    ).fetchall()
    ids.extend(int(r["id"]) for r in held)
    return ids
