# 03-changeflow（变流）

Changeflow — CDC / 变更流落库：事件入账 → 序/缺口裁决 → 幂等应用到 sink → 游标提交与对账。

**不是** Feed 多源归并（Feedstitch），**不是** 作业依赖编排（Batchgate）。

## 启动

```bash
docker compose up --build
```

| 入口 | 地址 |
| --- | --- |
| 前端 | http://localhost:5300 |
| API | http://localhost:10300 |

本地开发（可选）：

```bash
# backend
cd backend && pip install -r requirements.txt
set DATA_DIR=..\data
uvicorn app.main:app --reload --port 10300

# frontend
cd frontend && npm install && npm run dev
```

## 主链

配置变更流与夹具 → 触发 Apply → 按序应用 / 缺口裁决 → Sink + 游标 + 运行报告对账

## 缺口策略（互斥，全局设置）

在「应用台」保存 `gap_policy` 与 `gap_wait_window`，下一次 Apply 生效；同一次 run 只用一种语义：

- **stop_quarantine（停隔离）**：缺口即停，缺口起所有后续事件进隔离区（reason `gap_before_seq`），游标停在缺口前。
- **wait_window（等待）**：缺失 seq 数 ≤ 等待窗口 → 暂缓（账本 `deferred_gap`、run 状态 `waiting`）：游标不提交过缺口、事件不记已应用、不进 Sink，留待后续 run 重新裁决；缺失数 > 窗口 → 当场裁决进隔离区（reason `gap_beyond_wait_window`）。
- **skip_gap（跳过）**：跳过缺失 seq 继续应用更高 seq；跳过位点只记账本（`skipped_gap`，可审计），不进 applied_seqs / Sink。

对账口径：`loaded = applied + quarantined + skipped(重复) + deferred`；跳过位点为非加载事件，单列 `skipped_gap_seqs`；游标页展示跳过位点与等待中位点。

## 种子说明

- **Orders CDC（clean）**：JSONL，seq 1→5 连续 upsert/delete，首跑应全部应用、游标推进到 5。
- **Inventory CDC（clean）**：JSONL，seq 1→3 连续 upsert，独立实体键，用于第二路流演示。
- **Payments CDC（含缺口）**：JSONL，缺 seq 3 与 7-8，用于对比三种缺口策略下的游标与隔离结果（可在「变更流」页重置后换策略重放）。

打开「应用台」选流跑一次，再看账本 / 隔离区 / Sink / 游标页计数是否与本次报告一致。

## 技术栈

Python 3.12 + FastAPI + SQLite；Vue 3 + Vite + Nginx。

## 0-1 切口

底座实现「连续序幂等应用」主链；本题已落地 `gap_wait_or_skip`（缺口等待/跳过/停隔离三策略，见上节）。乱序缓冲、事务整批、双 sink 部分失败、毒丸选择性跳过等仍在 `backend/app/modules/*` 留空包，按题单逐题换主链路实现。
