from __future__ import annotations
from typing import Any
from fastapi import HTTPException
from app.db import connect, VISIBILITY_MODES, MODE_ATOMIC
from app.repositories import settings as settings_repo
from app.services import faults

FAULT_KEYS = ("fact_write_fail", "hold_flush")


def get_day() -> dict[str, Any]:
    conn = connect()
    try:
        return {"business_day": settings_repo.get(conn, "business_day")}
    finally:
        conn.close()


def set_day(body: dict[str, Any]) -> dict[str, Any]:
    day = (body.get("business_day") or "").strip()
    if len(day) != 10:
        raise HTTPException(400, "business_day 格式 YYYY-MM-DD")
    conn = connect(immediate=True)
    try:
        settings_repo.set_(conn, "business_day", day)
        conn.commit()
        return {"business_day": day}
    finally:
        conn.close()


def get_visibility() -> dict[str, Any]:
    conn = connect()
    try:
        mode = settings_repo.get(conn, "redeem_visibility_mode", MODE_ATOMIC)
        if mode not in VISIBILITY_MODES:
            mode = MODE_ATOMIC
        return {"redeem_visibility_mode": mode, "available": list(VISIBILITY_MODES)}
    finally:
        conn.close()


def set_visibility(body: dict[str, Any]) -> dict[str, Any]:
    """全局互斥：整个系统只贯彻一种核销双写可见性模式。"""
    mode = (body.get("redeem_visibility_mode") or "").strip()
    if mode not in VISIBILITY_MODES:
        raise HTTPException(400, f"redeem_visibility_mode 须为 {VISIBILITY_MODES} 之一")
    conn = connect(immediate=True)
    try:
        settings_repo.set_(conn, "redeem_visibility_mode", mode)
        conn.commit()
        return {"redeem_visibility_mode": mode}
    finally:
        conn.close()


def list_faults() -> dict[str, Any]:
    return faults.snapshot()


def set_fault(body: dict[str, Any]) -> dict[str, Any]:
    key = (body.get("key") or "").strip()
    if key not in FAULT_KEYS:
        raise HTTPException(400, f"故障键须为 {FAULT_KEYS} 之一")
    enabled = bool(body.get("enabled"))
    if key == "fact_write_fail":
        faults.set_fact_write_fail(enabled)
    else:
        seconds = None
        if "hold_seconds" in body:
            try:
                seconds = float(body["hold_seconds"])
            except (TypeError, ValueError):
                raise HTTPException(400, "hold_seconds 须为数字")
        faults.set_hold_flush(enabled, seconds)
    return faults.snapshot()
