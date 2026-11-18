"""故障注入开关（仅用于双写可见性路径演练）。

状态保存在进程内存而非 settings 表：故障标志若随业务事务写入，
会被 savepoint / rollback 一并回滚，导致一次性注入无法被消费。
通过 /api/debug/faults 查看与设置：
- fact_write_fail: 一次性，下一次事实侧写入（齐套请求路径 / 冲刷路径）抛错后自动复位
- hold_flush: 冲刷事务持写锁暂停 hold_seconds 秒，制造「冲刷中再钉」窗口
"""
from __future__ import annotations
import threading
import time


class FactWriteInjectedFailure(RuntimeError):
    """测试钩：事实侧写入被注入失败。"""


_STATE = {
    "fact_write_fail": False,
    "hold_flush": False,
    "hold_seconds": 2.0,
}
_LOCK = threading.Lock()


def set_fact_write_fail(enabled: bool) -> None:
    with _LOCK:
        _STATE["fact_write_fail"] = enabled


def set_hold_flush(enabled: bool, seconds: float | None = None) -> None:
    with _LOCK:
        _STATE["hold_flush"] = enabled
        if seconds is not None:
            _STATE["hold_seconds"] = max(0.1, float(seconds))


def snapshot() -> dict:
    with _LOCK:
        return {
            "faults": {
                "fact_write_fail": _STATE["fact_write_fail"],
                "hold_flush": _STATE["hold_flush"],
            },
            "hold_seconds": _STATE["hold_seconds"],
        }


def consume_fact_write_failure() -> None:
    """事实侧写入前调用；开关打开则抛错并自动复位（一次性，跨事务可靠）。"""
    with _LOCK:
        armed = _STATE["fact_write_fail"]
        if armed:
            _STATE["fact_write_fail"] = False
    if armed:
        raise FactWriteInjectedFailure("注入故障：核销事实写入失败")


def maybe_hold_flush() -> None:
    """冲刷事务内、插入 open 事实前调用，持写锁暂停。"""
    with _LOCK:
        enabled = _STATE["hold_flush"]
        seconds = _STATE["hold_seconds"]
    if enabled:
        time.sleep(seconds)
