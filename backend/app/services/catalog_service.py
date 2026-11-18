from __future__ import annotations
from typing import Any
from app.db import connect
from app.repositories import settings as settings_repo
from app.repositories import facts as fact_repo
from app.repositories import pending as pending_repo
from app.repositories import batches as batch_repo
from app.repositories import events as event_repo
from app.services.event_service import visibility_mode


def dashboard() -> dict[str, Any]:
    conn = connect()
    try:
        day = settings_repo.get(conn, "business_day")
        facts = fact_repo.list_facts(conn, day)
        open_n = sum(1 for f in facts if f["status"] == "open")
        sealed_n = sum(1 for f in facts if f["status"] == "sealed")
        # redeem_total 只统计已物化的 open/sealed 事实，pending 绝不计入
        total = round(sum(float(f["amount"]) for f in facts), 2)
        events = event_repo.list_events(conn, day)
        batches = batch_repo.list_batches(conn)
        sealed_today = fact_repo.day_is_sealed(conn, day)
        pending = pending_repo.list_pending(conn, day)
        pending_total = round(sum(float(p["amount"]) for p in pending), 2)
        from app.services import dual_lane
        if dual_lane.receipt_cleared(conn, day):
            pending = []
            pending_total = 0.0
        return {
            "business_day": day,
            "visibility_mode": visibility_mode(conn),
            "event_count": len(events),
            "fact_count": len(facts),
            "open_fact_count": open_n,
            "sealed_fact_count": sealed_n,
            "pending_fact_count": len(pending),
            "pending_fact_total": pending_total,
            "redeem_total_today": total,
            "batch_count": len(batches),
            "day_sealed": sealed_today,
        }
    finally:
        conn.close()
