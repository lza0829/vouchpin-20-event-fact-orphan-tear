# Vouchpin（券钉簿）

骨架：券事件入湖 → 核销双写（事件湖 + 核销事实）→ 事实钉扎成批次 → 批次互证。

本地：
- API: `cd backend && python -m uvicorn app.main:app --port 10800`
- Web: `cd frontend && npm install && npm run dev`（5800，`/api` 代理到 10800）

## 核销双写可见性（全局互斥，只贯彻一种）

设置键 `redeem_visibility_mode` 全局唯一，`/api/settings/visibility` 切换；系统任一时刻只按一种模式双写。

### 1. `atomic` 齐套可见（默认）

核销接口在一个 `BEGIN IMMEDIATE` 事务内同提交 redeem 事件与 open 事实，提交后回查两侧可查：

- 写入接口成功 **当且仅当** redeem 事件与 open 事实在同一提交内都可查；
- 任一侧失败 → 显式 `rollback`，两侧都不可见：接口回执失败、钉预览 open 不增加、库内无孤儿事件/孤儿事实；
- 故障注入 `fact_write_fail` 演练该路径。

### 2. `event_first` 事件先可见

接口成功只保证事件落库，事实侧以 `pending_facts` 行挂起：

- 钉预览与钉扎默认排除 pending（`open_facts_for_day` 只取 `status='open'`，结构性排除）；
- 总览给出 `pending_fact_count` / `pending_fact_total`，pending 不计入事实数与核销额；
- `POST /api/redeem/flush` 冲刷把 pending 落成 open；单条用 SAVEPOINT 包裹，
  冲刷失败仅回滚该条、pending 保留并累加 `attempts/last_error`，可再试；
- pending 绝不进入 sealed 明细；冲刷成功后预览/钉扎/总览计数同步下降。

## 协同面

| 面 | 行为 |
| --- | --- |
| 事件库 `coupon_events` | 两模式都先落事件；atomic 失败时随事务回滚 |
| 事实表 `redeem_facts` | 仅 open/sealed；钉扎只认 open，提交后回查 sealed 明细 |
| 挂起队列 `pending_facts` | 仅 event_first 使用，带 attempts/last_error，事件可溯源 |
| 冲刷入口 | `POST /api/redeem/flush`，支持按业务日，失败可重试 |
| 钉预览 | `GET /api/redeem/preview` 返回 open 与 pending 两套计数 |
| 总览 | `GET /api/dashboard` 含待物化计数与当前模式 |
| 页面回执 | 核销写入区分 open / pending；钉扎失败明确报错，不把缺事实当已钉成功 |

## 故障注入（演练钩）

进程内存开关（不随业务事务回滚），`GET/PUT /api/debug/faults`：

- `fact_write_fail`：一次性，下一次事实侧写入抛错。覆盖①齐套模式请求路径（两侧回滚、回执失败、无孤儿）②冲刷路径（该条保持 pending 可再试）；
- `hold_flush` + `hold_seconds`：冲刷事务持写锁暂停，制造「冲刷中再钉」并发窗口；此时钉扎返回 400（无 open）或 503（写锁冲突），绝不产生半截批次或把 pending 误钉。

## 端到端演练

```
cd backend
python -m venv .venv && .venv/bin/pip install -r requirements.txt
DATA_DIR=/tmp/vp-e2e .venv/bin/python tests/test_dual_write.py
```

真实 HTTP + 并发线程，覆盖：齐套失败无孤儿、pending 全链路（排除/冲刷/重试/计数同步）、
冲刷中再钉、全局互斥与孤儿终态（36 项断言）。
