from __future__ import annotations
from typing import Any
from fastapi import APIRouter, Query
from app.services import redeem_service as svc

router = APIRouter(tags=["redeem"])


@router.get("/facts")
def facts(business_day: str | None = Query(None)):
    return svc.list_facts(business_day)


@router.get("/redeem/preview")
def preview(business_day: str | None = Query(None)):
    return svc.preview(business_day)


@router.post("/redeem/flush")
def flush(body: dict[str, Any] | None = None):
    return svc.flush(body or {})


@router.get("/redeem/lanes")
def lanes(business_day: str | None = Query(None)):
    from app.db import connect
    from app.repositories import pending as pending_repo
    from app.repositories import facts as fact_repo
    from app.repositories import settings as settings_repo
    conn = connect()
    try:
        day = business_day or settings_repo.get(conn, "business_day")
        facts = fact_repo.list_facts(conn, day)
        return {
            "business_day": day,
            "open_count": sum(1 for f in facts if f["status"] == "open"),
            "held_count": sum(1 for f in facts if f["status"] == "held"),
            "dangling_event_ids": [f["id"] for f in facts if int(f["event_id"] or 0) == 0],
            "pending_count": pending_repo.pending_count(conn, day),
        }
    finally:
        conn.close()


@router.post("/redeem/seal")
def seal(body: dict[str, Any]):
    return svc.seal(body)


@router.get("/batches")
def batches():
    return svc.list_batches()
