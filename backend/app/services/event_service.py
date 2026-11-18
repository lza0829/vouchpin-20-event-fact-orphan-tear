from __future__ import annotations
import sqlite3
from datetime import datetime
from typing import Any
from fastapi import HTTPException
from app.db import connect, MODE_ATOMIC, MODE_EVENT_FIRST
from app.repositories import settings as settings_repo
from app.repositories import events as event_repo
from app.repositories import facts as fact_repo
from app.repositories import pending as pending_repo
from app.repositories import campaigns as camp_repo
from app.services import faults


def visibility_mode(conn) -> str:
    mode = settings_repo.get(conn, "redeem_visibility_mode", MODE_ATOMIC)
    return mode if mode in (MODE_ATOMIC, MODE_EVENT_FIRST) else MODE_ATOMIC


def list_campaigns() -> list[dict[str, Any]]:
    conn = connect()
    try:
        return camp_repo.list_all(conn)
    finally:
        conn.close()


def list_events(business_day: str | None = None) -> list[dict[str, Any]]:
    conn = connect()
    try:
        day = business_day or settings_repo.get(conn, "business_day")
        return event_repo.list_events(conn, day)
    finally:
        conn.close()


def _redeem_amount(conn, camp: str, body: dict[str, Any]) -> float:
    amount = float(body.get("amount") or 0)
    if amount <= 0:
        camps = {c["code"]: c for c in camp_repo.list_all(conn)}
        if camp in camps:
            amount = float(camps[camp]["face_value"])
    return amount


def _insert_open_fact(conn, **fields: Any) -> int:
    """所有 open 事实写入的唯一入口，故障钩在此生效（请求路径与冲刷路径共用）。"""
    faults.consume_fact_write_failure()
    return fact_repo.insert_fact(conn, **fields)


def add_event(body: dict[str, Any]) -> dict[str, Any]:
    conn = connect()
    try:
        try:
            # 写锁获取纳入捕获：冲刷持锁期间写入返回明确 503 而非裸 500
            conn.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as e:
            raise HTTPException(503, f"写入冲突（库正被冲刷占用，请重试）: {e}") from e
        day = body.get("business_day") or settings_repo.get(conn, "business_day")
        et = body.get("event_type")
        if et not in {"issue", "redeem", "void"}:
            raise HTTPException(400, "event_type 须为 issue/redeem/void")
        code = (body.get("coupon_code") or "").strip()
        camp = (body.get("campaign_code") or "").strip()
        if not code or not camp:
            raise HTTPException(400, "coupon_code / campaign_code 必填")
        if et == "redeem" and fact_repo.day_is_sealed(conn, day):
            raise HTTPException(409, f"业务日 {day} 核销已钉扎，禁止再写入核销")
        amount = _redeem_amount(conn, camp, body) if et == "redeem" else float(body.get("amount") or 0)
        occurred = body.get("occurred_at") or datetime.now().replace(microsecond=0).isoformat()
        mode = visibility_mode(conn)

        if et == "redeem":
            from app.services import dual_lane
            return dual_lane.write_redeem(
                conn,
                day=day,
                code=code,
                camp=camp,
                amount=amount,
                occurred=occurred,
                mode=mode,
                note=body.get("note") or "",
            )

        eid = event_repo.insert_event(
            conn,
            event_type=et,
            coupon_code=code,
            campaign_code=camp,
            amount=amount,
            business_day=day,
            occurred_at=occurred,
            note=body.get("note") or "",
        )

        result: dict[str, Any] = {
            "event_id": eid,
            "business_day": day,
            "visibility_mode": mode,
            "fact_id": None,
            "pending_id": None,
            "fact_status": None,
        }

        if et != "redeem":
            conn.commit()
            return result

        if mode == MODE_ATOMIC:
            # 齐套可见：事件与 open 事实同一提交，任一失败两侧皆不可见
            try:
                fact_id = _insert_open_fact(
                    conn,
                    coupon_code=code,
                    campaign_code=camp,
                    amount=amount,
                    business_day=day,
                    event_id=eid,
                    status="open",
                )
            except faults.FactWriteInjectedFailure as e:
                conn.rollback()
                raise HTTPException(502, f"核销双写失败（事实侧注入故障），事件与事实均未提交: {e}") from e
            except Exception as e:
                conn.rollback()
                raise HTTPException(409, f"核销事实写入失败，事件已回滚（可能券码已核销）: {e}") from e
            conn.commit()
            # 提交后回查：两侧必须同刻可查，否则按双写失败处理
            ev = conn.execute("SELECT 1 FROM coupon_events WHERE id=?", (eid,)).fetchone()
            fct = conn.execute("SELECT 1 FROM redeem_facts WHERE id=?", (fact_id,)).fetchone()
            if not ev or not fct:
                raise HTTPException(500, "核销双写可见性校验失败：提交后存在单侧缺失")
            result.update(fact_id=fact_id, fact_status="open")
            return result

        # 事件先可见：事件与 pending 挂起同事务落库，事实侧不产生 open
        try:
            pid = pending_repo.insert_pending(
                conn,
                event_id=eid,
                coupon_code=code,
                campaign_code=camp,
                amount=amount,
                business_day=day,
            )
        except Exception as e:
            conn.rollback()
            raise HTTPException(409, f"pending 挂起失败，事件已回滚: {e}") from e
        conn.commit()
        # 回查：事件可见、pending 可见、open 事实不得提前出现（防孤儿事实）
        ev = conn.execute("SELECT 1 FROM coupon_events WHERE id=?", (eid,)).fetchone()
        pend = conn.execute("SELECT 1 FROM pending_facts WHERE id=?", (pid,)).fetchone()
        early = conn.execute("SELECT 1 FROM redeem_facts WHERE event_id=?", (eid,)).fetchone()
        if not ev or not pend or early:
            raise HTTPException(500, "事件先可见模式一致性校验失败")
        result.update(pending_id=pid, fact_status="pending")
        return result
    finally:
        conn.close()
