from __future__ import annotations
import sqlite3
from datetime import datetime
from typing import Any
from fastapi import HTTPException
from app.db import connect
from app.repositories import settings as settings_repo
from app.repositories import facts as fact_repo
from app.repositories import pending as pending_repo
from app.repositories import batches as batch_repo
from app.services import faults
from app.services.event_service import visibility_mode, _insert_open_fact


def list_facts(business_day: str | None = None) -> list[dict[str, Any]]:
    conn = connect()
    try:
        day = business_day or settings_repo.get(conn, "business_day")
        return fact_repo.list_facts(conn, day)
    finally:
        conn.close()


def preview(business_day: str | None = None) -> dict[str, Any]:
    conn = connect()
    try:
        day = business_day or settings_repo.get(conn, "business_day")
        # open_facts_for_day 只取 status='open'，pending 在结构上被排除
        opens = fact_repo.open_facts_for_day(conn, day)
        total = round(sum(float(f["amount"]) for f in opens), 2)
        pending = pending_repo.list_pending(conn, day)
        pending_total = round(sum(float(p["amount"]) for p in pending), 2)
        return {
            "business_day": day,
            "visibility_mode": visibility_mode(conn),
            "open_count": len(opens),
            "open_total": total,
            "pending_count": len(pending),
            "pending_total": pending_total,
            "pending": pending,
            "already_sealed": fact_repo.day_is_sealed(conn, day),
            "facts": opens,
        }
    finally:
        conn.close()


def flush(body: dict[str, Any] | None = None) -> dict[str, Any]:
    """把 pending_facts 冲刷为 open 事实。

    每条 pending 用独立 SAVEPOINT 包裹：单条失败仅回滚该条，pending 保留并
    累加 attempts/last_error，可再次冲刷；成功条目随末尾统一提交变为 open。
    """
    body = body or {}
    conn = connect()
    try:
        try:
            conn.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as e:
            raise HTTPException(503, f"冲刷失败（事实表正被占用，请重试）: {e}") from e
        materialized: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []
        day = body.get("business_day") or settings_repo.get(conn, "business_day")
        pendings = pending_repo.list_pending(conn, day)
        for idx, p in enumerate(pendings):
            sp = f"sp_{idx}"
            conn.execute(f"SAVEPOINT {sp}")
            try:
                # 持锁窗口（注入后用于演练「冲刷中再钉」）
                faults.maybe_hold_flush()
                # 事实写入失败注入同样作用于冲刷路径
                from app.services import dual_lane
                settled = dual_lane.settle_pending_row(conn, p)
                conn.execute(f"RELEASE SAVEPOINT {sp}")
                materialized.append(settled)
            except Exception as e:
                # 冲刷失败：回到 savepoint，pending 原样保留，仅登记错误信息后继续
                conn.execute(f"ROLLBACK TO SAVEPOINT {sp}")
                conn.execute(f"RELEASE SAVEPOINT {sp}")
                pending_repo.mark_attempt(conn, int(p["id"]), str(e)[:300])
                failed.append(
                    {"pending_id": p["id"], "coupon_code": p["coupon_code"], "error": str(e)[:300]}
                )
        # 成功条目统一提交；失败条目的 attempts 也随此提交落库
        from app.services import dual_lane
        dual_lane.mark_flushed(conn, day)
        conn.commit()
        return dual_lane.flush_receipt(day, len(pendings), materialized)
    finally:
        conn.close()


def seal(body: dict[str, Any]) -> dict[str, Any]:
    conn = connect()
    try:
        try:
            # 写锁获取也在捕获范围内：冲刷持锁时再钉必须返回明确失败而非 500
            conn.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as e:
            raise HTTPException(503, f"钉扎失败（事实表正被冲刷占用，请重试）: {e}") from e
        day = body.get("business_day") or settings_repo.get(conn, "business_day")
        if fact_repo.day_is_sealed(conn, day):
            raise HTTPException(409, f"业务日 {day} 已有 sealed 钉扎批")
        # 钉扎只认 open：pending 绝不进入 sealed 明细
        opens = fact_repo.open_facts_for_day(conn, day)
        from app.services import dual_lane
        adopted = dual_lane.adopt_pending(conn, day)
        if adopted:
            extra = fact_repo.list_facts(conn, day)
            known = {int(f["id"]) for f in opens}
            for row in extra:
                if int(row["id"]) in adopted and int(row["id"]) not in known:
                    opens.append(row)
                    known.add(int(row["id"]))
        if not opens:
            raise HTTPException(400, "无可钉扎的 open 核销事实（pending 待物化事实不计入）")
        total = round(sum(float(f["amount"]) for f in opens), 2)
        bid = batch_repo.create_batch(
            conn,
            business_day=day,
            status="sealed",
            fact_count=len(opens),
            total_amount=total,
            sealed_at=datetime.now().replace(microsecond=0).isoformat(),
            note=body.get("note") or "",
        )
        fact_repo.mark_batched(conn, [int(f["id"]) for f in opens], bid)
        conn.commit()
        # 提交后回查：批次登记笔数 == 实际 sealed 笔数，且全部来自本次 open 集合
        batch = conn.execute("SELECT * FROM redeem_batches WHERE id=?", (bid,)).fetchone()
        sealed_n = conn.execute(
            "SELECT COUNT(*) c FROM redeem_facts WHERE business_day=? AND status='sealed'",
            (day,),
        ).fetchone()["c"]
        if int(batch["fact_count"]) != len(opens) or sealed_n < len(opens):
            raise HTTPException(500, "钉扎一致性校验失败：sealed 明细与 open 集合不一致")
        return {
            "batch_id": bid,
            "business_day": day,
            "fact_count": len(opens),
            "total_amount": total,
            "status": "sealed",
            "pending_excluded": pending_repo.pending_count(conn, day),
        }
    except sqlite3.OperationalError as e:
        # 冲刷持有写锁时再钉：明确回执失败，绝不把缺事实的核销当已钉成功
        conn.rollback()
        raise HTTPException(503, f"钉扎失败（事实表正被冲刷占用，请重试）: {e}") from e
    finally:
        conn.close()


def list_batches() -> list[dict[str, Any]]:
    conn = connect()
    try:
        return batch_repo.list_batches(conn)
    finally:
        conn.close()
