"""核销双写可见性端到端演练（真实 HTTP，含故障注入与并发）。

运行：
  DATA_DIR=/tmp/vp-e2e .venv/bin/python tests/test_dual_write.py

覆盖：
  A. 齐套可见 atomic：事实写入失败注入 -> 两侧不可见、无孤儿事件/事实、预览不增、回执失败
  B. 事件先可见 event_first：pending 排除预览/钉扎/总览；冲刷失败保持 pending 可再试；成功后计数同步下降
  C. 冲刷中再钉：持锁并发 seal -> 503、无半截批次、pending 不被误钉；冲刷完成后再钉成功
"""
from __future__ import annotations
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

BASE = f"http://127.0.0.1:{os.environ.get('TEST_PORT', '10899')}"
DATA_DIR = os.environ.get("DATA_DIR", "/tmp/vp-e2e")
DB_PATH = Path(DATA_DIR) / "clipbond.db"

PASS = 0
FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name} {detail}")


def http(method: str, path: str, body=None, expect_error: bool = False):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BASE + path, data=data, method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            payload = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            payload = raw
        if expect_error:
            return e.code, payload
        raise AssertionError(f"{method} {path} 意外失败 {e.code}: {payload}") from None


def wait_port(host: str, port: int, timeout: float = 15.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1):
                return
        except OSError:
            time.sleep(0.2)
    raise RuntimeError(f"端口 {port} 未就绪")


def reset_faults() -> None:
    http("PUT", "/api/debug/faults", {"key": "fact_write_fail", "enabled": False})
    http("PUT", "/api/debug/faults", {"key": "hold_flush", "enabled": False})


def set_mode(mode: str) -> None:
    _, r = http("PUT", "/api/settings/visibility", {"redeem_visibility_mode": mode})
    assert r["redeem_visibility_mode"] == mode


def set_day(day: str) -> None:
    http("PUT", "/api/settings/day", {"business_day": day})


def sql(query: str):
    import sqlite3
    conn = sqlite3.connect(DB_PATH)
    try:
        return conn.execute(query).fetchall()
    finally:
        conn.close()


def add_redeem(code: str, day: str, expect_error: bool = False):
    return http("POST", "/api/events", {
        "event_type": "redeem", "coupon_code": code,
        "campaign_code": "NEW10", "business_day": day,
    }, expect_error=expect_error)


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def test_atomic(day: str) -> None:
    section("A. 齐套可见 atomic（含事实写入失败注入）")
    set_mode("atomic")
    reset_faults()
    set_day(day)

    # A1 正常核销：事件与 open 事实同提交，预览 +1
    _, r = add_redeem("A-1001", day)
    check("成功回执同时带 event_id 与 open fact_id",
          r["event_id"] and r["fact_id"] and r["fact_status"] == "open", str(r))
    _, prev = http("GET", f"/api/redeem/preview?business_day={day}")
    check("预览 open_count=1（齐套后立即可见）", prev["open_count"] == 1 and prev["pending_count"] == 0, str(prev))

    # A2 注入事实写入失败：接口失败，事件与事实两侧都不可见
    http("PUT", "/api/debug/faults", {"key": "fact_write_fail", "enabled": True})
    code, err = add_redeem("A-1002", day, expect_error=True)
    check("故障下回执失败(502)", code == 502, f"code={code} body={err}")
    reset_faults()
    _, prev2 = http("GET", f"/api/redeem/preview?business_day={day}")
    check("失败后预览 open 不增加（仍为1）", prev2["open_count"] == 1, str(prev2))
    ev_n = sql(f"SELECT COUNT(*) FROM coupon_events WHERE coupon_code='A-1002'")[0][0]
    fact_n = sql(f"SELECT COUNT(*) FROM redeem_facts WHERE coupon_code='A-1002'")[0][0]
    pend_n = sql(f"SELECT COUNT(*) FROM pending_facts WHERE coupon_code='A-1002'")[0][0]
    check("库内无孤儿事件", ev_n == 0, f"events={ev_n}")
    check("库内无孤儿事实", fact_n == 0 and pend_n == 0, f"facts={fact_n} pending={pend_n}")

    # A3 故障清除后同券码可正常写入（失败未留下半态）
    _, r3 = add_redeem("A-1003", day)
    check("恢复后新核销成功", r3["fact_id"] is not None, str(r3))

    # A4 钉扎：sealed 明细 == open 集合，当日无 pending 被误钉
    _, seal = http("POST", "/api/redeem/seal", {"business_day": day})
    check("钉扎批次笔数=2（A-1002 失败不占位）", seal["fact_count"] == 2, str(seal))
    sealed_rows = sql(f"SELECT coupon_code FROM redeem_facts WHERE business_day='{day}' AND status='sealed'")
    check("sealed 明细无缺失/无串号",
          sorted(r[0] for r in sealed_rows) == ["A-1001", "A-1003"], str(sealed_rows))
    _, dash = http("GET", "/api/dashboard")
    check("总览 open 归零、sealed=2", dash["open_fact_count"] == 0 and dash["sealed_fact_count"] == 2, str(dash))

    # A5 当日密封后再核销被拒
    code5, _ = add_redeem("A-1004", day, expect_error=True)
    check("密封日后拒绝新核销(409)", code5 == 409)


def test_event_first(day: str) -> None:
    section("B. 事件先可见 event_first（pending/冲刷/重试）")
    set_mode("event_first")
    reset_faults()
    set_day(day)

    # B1 核销成功只保证事件落库，事实以 pending 挂起
    _, r1 = add_redeem("B-2001", day)
    check("回执为 pending（无 open fact_id）",
          r1["fact_id"] is None and r1["pending_id"] and r1["fact_status"] == "pending", str(r1))
    ev = sql(f"SELECT COUNT(*) FROM coupon_events WHERE coupon_code='B-2001'")[0][0]
    fact = sql(f"SELECT COUNT(*) FROM redeem_facts WHERE coupon_code='B-2001'")[0][0]
    check("事件已可见", ev == 1)
    check("open 事实未提前出现（无孤儿事实）", fact == 0)

    _, r2 = add_redeem("B-2002", day)
    _, prev = http("GET", f"/api/redeem/preview?business_day={day}")
    check("预览默认排除 pending（open=0, pending=2）",
          prev["open_count"] == 0 and prev["pending_count"] == 2, str(prev))
    _, dash = http("GET", "/api/dashboard")
    check("总览给出待物化计数=2 且不计入事实/核销额",
          dash["pending_fact_count"] == 2 and dash["fact_count"] == 0
          and dash["redeem_total_today"] == 0 and dash["open_fact_count"] == 0, str(dash))

    # B2 pending 存在时钉扎：不得把 pending 当已钉成功
    code, err = http("POST", "/api/redeem/seal", {"business_day": day}, expect_error=True)
    check("无 open 时钉扎被拒(400)，pending 不算成功", code == 400, f"{code} {err}")
    batches = sql(f"SELECT COUNT(*) FROM redeem_batches WHERE business_day='{day}'")[0][0]
    check("没有产生半截批次", batches == 0)

    # B3 注入冲刷失败（一次性，作用于首条 pending）：失败项保持 pending、attempts+1 可再试
    http("PUT", "/api/debug/faults", {"key": "fact_write_fail", "enabled": True})
    _, fr = http("POST", "/api/redeem/flush", {"business_day": day})
    check("冲刷回执 materialized=1 failed=1 remaining=1",
          fr["materialized_count"] == 1 and fr["failed_count"] == 1 and fr["remaining_pending"] == 1, str(fr))
    check("同次冲刷后续 pending 成功（故障一次性，仅作用一次）",
          fr["materialized"][0]["coupon_code"] == "B-2002", str(fr))
    left = sql(f"SELECT coupon_code, attempts, last_error FROM pending_facts WHERE business_day='{day}'")
    check("失败项保持 pending 且 attempts/last_error 已登记，可再试",
          len(left) == 1 and left[0][0] == "B-2001" and left[0][1] >= 1 and bool(left[0][2]), str(left))
    _, prev_b = http("GET", f"/api/redeem/preview?business_day={day}")
    check("冲刷部分成功：open=1、pending=1",
          prev_b["open_count"] == 1 and prev_b["pending_count"] == 1, str(prev_b))

    # B4 再次冲刷（故障已自动复位）：剩余 pending -> open，三处计数同步下降
    _, fr2 = http("POST", "/api/redeem/flush", {"business_day": day})
    check("二次冲刷成功 materialized=1 remaining=0",
          fr2["materialized_count"] == 1 and fr2["remaining_pending"] == 0, str(fr2))
    _, prev_c = http("GET", f"/api/redeem/preview?business_day={day}")
    _, dash_c = http("GET", "/api/dashboard")
    check("预览 open=2、pending=0", prev_c["open_count"] == 2 and prev_c["pending_count"] == 0, str(prev_c))
    check("总览待物化清零、open 升到2",
          dash_c["pending_fact_count"] == 0 and dash_c["open_fact_count"] == 2
          and dash_c["fact_count"] == 2 and dash_c["redeem_total_today"] == 20.0, str(dash_c))

    # B5 pending 绝不进入 sealed 明细
    _, seal = http("POST", "/api/redeem/seal", {"business_day": day})
    check("钉扎笔数=2（不含任何 pending）", seal["fact_count"] == 2 and seal["pending_excluded"] == 0, str(seal))
    sealed_rows = sql(f"SELECT coupon_code FROM redeem_facts WHERE business_day='{day}' AND status='sealed'")
    check("sealed 明细仅 B-2001/B-2002",
          sorted(r[0] for r in sealed_rows) == ["B-2001", "B-2002"], str(sealed_rows))


def test_flush_while_seal(day: str) -> None:
    section("C. 冲刷中再钉（并发路径）")
    set_mode("event_first")
    reset_faults()
    set_day(day)
    for i, code in enumerate(("C-3001", "C-3002", "C-3003")):
        add_redeem(code, day)
    _, prev0 = http("GET", f"/api/redeem/preview?business_day={day}")
    check("前置：3 笔全部 pending、open=0",
          prev0["pending_count"] == 3 and prev0["open_count"] == 0, str(prev0))

    # 冲刷事务持写锁 8 秒
    http("PUT", "/api/debug/faults",
         {"key": "hold_flush", "enabled": True, "hold_seconds": 8})
    flush_result = {}
    barrier = threading.Barrier(2)

    def do_flush():
        barrier.wait()
        _, flush_result["r"] = http("POST", "/api/redeem/flush", {"business_day": day})

    with ThreadPoolExecutor(max_workers=2) as pool:
        fut = pool.submit(do_flush)
        barrier.wait()
        time.sleep(1.5)  # 确保冲刷已进入持锁事务
        code, err = http("POST", "/api/redeem/seal", {"business_day": day}, expect_error=True)
        # 冲刷进行中：pending 尚不可钉，且拿不到写锁
        check("冲刷中再钉回执失败（400 无 open 或 503 锁冲突），绝不报成功",
              code in (400, 503), f"code={code} body={err}")
        mid_batches = sql(f"SELECT COUNT(*) FROM redeem_batches WHERE business_day='{day}'")[0][0]
        mid_pending = sql(f"SELECT COUNT(*) FROM pending_facts WHERE business_day='{day}'")[0][0]
        check("冲刷期间无半截批次、pending 未被误钉", mid_batches == 0 and mid_pending == 3)
        fut.result(timeout=30)

    http("PUT", "/api/debug/faults", {"key": "hold_flush", "enabled": False})
    check("持锁冲刷最终成功 materialized=3", flush_result["r"]["materialized_count"] == 3, str(flush_result["r"]))
    _, prev1 = http("GET", f"/api/redeem/preview?business_day={day}")
    check("冲刷后 open=3、pending=0", prev1["open_count"] == 3 and prev1["pending_count"] == 0, str(prev1))

    _, seal = http("POST", "/api/redeem/seal", {"business_day": day})
    sealed_n = sql(f"SELECT COUNT(*) FROM redeem_facts WHERE business_day='{day}' AND status='sealed'")[0][0]
    check("冲刷完成后再钉成功，sealed=3", seal["fact_count"] == 3 and sealed_n == 3, str(seal))


def test_global_mutex() -> None:
    section("D. 全局互斥与孤儿终态")
    code, _ = http("PUT", "/api/settings/visibility",
                   {"redeem_visibility_mode": "bogus"}, expect_error=True)
    check("拒绝未知可见性模式(400)", code == 400)
    _, r = http("GET", "/api/settings/visibility")
    check("模式只能是二选一", r["redeem_visibility_mode"] in ("atomic", "event_first")
          and set(r["available"]) == {"atomic", "event_first"}, str(r))
    orphans_f = sql(
        "SELECT COUNT(*) FROM redeem_facts f WHERE NOT EXISTS "
        "(SELECT 1 FROM coupon_events e WHERE e.id=f.event_id)")[0][0]
    orphans_p = sql(
        "SELECT COUNT(*) FROM pending_facts p WHERE NOT EXISTS "
        "(SELECT 1 FROM coupon_events e WHERE e.id=p.event_id)")[0][0]
    check("全库无孤儿事实/孤儿 pending（事件侧均可溯源）", orphans_f == 0 and orphans_p == 0,
          f"fact_orphans={orphans_f} pending_orphans={orphans_p}")


def main() -> int:
    Path(DATA_DIR).mkdir(parents=True, exist_ok=True)
    port = int(os.environ.get("TEST_PORT", "10899"))
    env = {**os.environ, "DATA_DIR": DATA_DIR}
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port), "--log-level", "warning"],
        cwd=Path(__file__).resolve().parents[1], env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    try:
        wait_port("127.0.0.1", port)
        test_atomic("2026-11-11")
        test_event_first("2026-11-12")
        test_flush_while_seal("2026-11-13")
        test_global_mutex()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)
        if FAIL:
            print("\n--- server log ---\n" + (proc.stdout.read() if proc.stdout else ""))
    print(f"\n结果：{PASS} 通过 / {FAIL} 失败")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
