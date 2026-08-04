# Stock AI — A 股 AI 多智能体模拟交易系统

参考 [TradingAgents](https://github.com/TauricResearch/TradingAgents) / [TradingAgents-CN](https://github.com/hsliuping/TradingAgents-CN) 架构的多 Agent 辩论式 LLM 决策引擎 + A 股模拟盘自动交易。

- **数据源**: [AKShare](https://github.com/akfamily/akshare)(免费、无 Token)
- **LLM**: 任意 OpenAI 兼容接口(DeepSeek / 通义 / Kimi / GPT 等)
- **模拟盘**: 虚拟资金 ¥1,000,000,遵守 A 股 T+1 / 整手 / 涨跌停 / 佣金印花税规则
- **技术栈**: FastAPI + SQLite + APScheduler / Vue 3 + Element Plus + ECharts

> ⚠️ 本项目仅供学习研究,不构成任何投资建议。模拟盘成交为理想化撮合,与实盘存在差异。

## 多模型锦标赛

每个 LLM 模型拥有**独立虚拟账户**,同一股池各自决策,收益曲线同屏对比;还可自定义**合议组合**(如 A+B、A+B+C):组合决策由成员模型当日最终决策多数票合成(过半才动手,仓位取获胜方均值),零额外 LLM 调用,同样挂独立账户参赛。

## 决策流程

**每交易日一次全量深度决策**(默认 14:35),每个模型对股池 + 持仓中的每只股票执行:

```
市场环境分析师 (大盘) ─────────────────┐
技术分析师 ─┐                        ↓
基本面分析师 ─┼→ 多空辩论 (2轮) → 交易员(注入大盘+历史反思) → 风控经理 → 硬性风控 → 模拟撮合
新闻情绪分析师 ─┘                                                    ↓
                                                    反思环节: 复盘近期交易 → 经验教训入库
```

**每日自动选股**(默认 14:05,决策前 30 分钟):新浪全市场快照规则初筛(排除 ST/北交所、价格 3-100 元、成交额 ≥2 亿、当日涨跌 -3%~+7%,按成交额取前 30)→ 一次 LLM 调用结合大盘环境精选入**共享股池**(上限 8 只,含手动股)。AI 自动入池的股票若无任何账户持仓且连续 3 个选股日未被继续看好则自动移除;手动添加的永不自动移除。选股推理全文可在「决策记录」回放,股池页也可手动触发「AI 选股」。

**盘中监控**(每 15 分钟,纯规则零 LLM):持仓浮盈 ≥+15% 或浮亏 ≤-8% 时触发单次 LLM 复审(AI 自主决定卖出/持有,每股每日 1 次);浮亏 ≤-15% 为深亏,复审频率提升(至少间隔 1 小时),**无任何无条件强制清仓**,决定权始终在 AI。

代码层硬性风控(不依赖 LLM):单票 ≤30% 总资产、单次买入 ≤50% 可用资金、总仓位 ≤90%。

## 快速开始 (Docker,推荐)

```bash
mkdir stock-ai && cd stock-ai
curl -O https://raw.githubusercontent.com/xp9477/stock-ai/main/docker-compose.yml
curl -o .env https://raw.githubusercontent.com/xp9477/stock-ai/main/.env.example
# 编辑 .env 填入你的 LLM_API_KEY
docker compose up -d       # http://localhost:8000
```

- 数据(SQLite)持久化在 `./data/stock_ai.db`
- **更新版本**: `docker compose pull && docker compose up -d`
- 镜像由 GitHub Actions 在每次 push 到 main 后自动构建: `ghcr.io/xp9477/stock-ai:latest`(amd64/arm64)

## 本地开发

### 1. 后端

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env   # 编辑填入你的 LLM_API_KEY
uvicorn app.main:app --port 8000
```

### 2. 前端

```bash
cd frontend
npm install
npm run dev            # http://localhost:5173
```

### 3. 使用

1. 打开前端 → 「股池管理」添加自选股(如 `600519`、`300750`,不支持 ST/北交所),或点「AI 选股」让 AI 自动挑选
2. 「模型管理」调整参赛模型与合议组合(默认三模型 + 三模合议)
3. 点击右上角「立即运行一轮」,或等待每日定时决策(默认交易日 14:35)
4. 「决策记录」按模型查看各 Agent 报告、辩论、大盘环境与反思;「仪表盘」看排行榜与多曲线对比

## 赛马底座（已落地）

北极星：**可验证 edge**（夏普/回撤/超额），不是胜率。规则因子组 vs AI 同场；AI 吃同一 X1 事实底稿（含 S2 因子）。

| 模块 | 说明 |
|---|---|
| S2 因子 | 短动量 / 中动量 / 低波动 / EP / BP / 质量(ROE)，截面 z 分等权合成，周频前 10 等权 |
| 回测 | `POST /api/backtest/run`：池内等权锚 + 因子周频；指标含夏普、最大回撤、样本门槛标记 |
| 事实底稿 | `GET /api/factsheet/{code}`；决策流水线注入全部 AI 臂 |
| 账本 | `trade_ledger` + `GET /api/ledger/stats`（100 笔平仓门槛） |
| 规则组前瞻 | `S2周频前10` + `池内等权` 独立模拟账户；`POST /api/rules/rebalance`；周一 14:50 自动调仓 |
| 盘中分层 | 深亏强制砍；浅止损/止盈仅告警 |

数据分层：

- **主源**：同花顺[扶摇](https://fuyao.aicubes.cn/)（`FUYAO_API_KEY`）— 日 K / 估值 / 财务指标 / 后续 ETF  
- **新闻**：Vibe 路线公开 RSS（零 Key，本地抓取）  
- **不做**免费源降级/交叉校验；不够用再上 Tushare

## 配置(backend/.env)

| 变量 | 说明 | 默认 |
|---|---|---|
| `LLM_BASE_URL` | OpenAI 兼容接口地址(所有模型共用) | `https://api.deepseek.com` |
| `LLM_API_KEY` | API Key | - |
| `FUYAO_API_KEY` | 同花顺扶摇 API Key（主数据源） | - |
| `INITIAL_CASH` | 每个模型账户初始虚拟资金 | `1000000` |
| `SCHEDULE_ENABLED` | 开启定时调度 | `true` |
| `STOCK_SELECT_ENABLED` | 开启每日自动选股 | `true` |
| `STOCK_SELECT_TIME` | 自动选股时刻 | `14:05` |
| `POOL_MAX` | 共享股池上限 | `30` |
| `FACTOR_TOP_N` | 因子组合持仓只数 | `10` |
| `DAILY_DECISION_TIME` | 每日全量决策时刻 | `14:35` |
| `MONITOR_INTERVAL_MINUTES` | 盘中监控间隔(分钟) | `15` |
| `TAKE_PROFIT_REVIEW_PCT` | 止盈警戒线（仅告警） | `0.15` |
| `STOP_LOSS_REVIEW_PCT` | 止损警戒线（仅告警） | `-0.08` |
| `DEEP_LOSS_PCT` | 深亏强制砍仓阈值 | `-0.15` |
| `RACE_MIN_TRADE_DAYS` | 样本门槛：交易日 | `60` |
| `RACE_MIN_CLOSED_TRADES` | 样本门槛：平仓笔数 | `100` |
| `DB_PATH` | SQLite 路径(Docker 内为 `/data/stock_ai.db`) | `stock_ai.db` |

模型名单不在 `.env` 配置,在前端「模型管理」页维护(默认种子: Grok 4.5 / Opus 5 / Fable 5 + 三模合议)。

## 测试

```bash
cd backend && .venv/bin/pytest tests/ -v
```

覆盖:撮合规则(T+1/整手/涨跌停/费用)、风控硬规则、技术指标计算、Agent 流水线编排与 JSON 解析容错(mock LLM)。

## 说明

- 非交易时段手动触发仍可运行,以最近价格撮合,便于调试。
- 每只股票每模型一轮约 9 次 LLM 调用(另每模型每轮 1 次大盘 + 1 次反思),合议组合零调用,注意 token 成本。
- **从 v1 升级**: 数据库 schema 有破坏式变更,请删除旧 `data/stock_ai.db` 后重启。
- 数据库为 SQLite 单文件(本地开发 `backend/stock_ai.db`,Docker 部署 `./data/stock_ai.db`),删除即全部重置。

## 维护者说明

- CI: `.github/workflows/docker.yml`,push 到 main 自动构建 amd64 镜像并推送 `latest` 与 `sha-<commit>` 双 tag 到 GHCR(包已继承仓库公开可见性,可匿名拉取)。
