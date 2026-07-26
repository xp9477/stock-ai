# Stock AI — A 股 AI 多智能体模拟交易系统

参考 [TradingAgents](https://github.com/TauricResearch/TradingAgents) / [TradingAgents-CN](https://github.com/hsliuping/TradingAgents-CN) 架构的多 Agent 辩论式 LLM 决策引擎 + A 股模拟盘自动交易。

- **数据源**: [AKShare](https://github.com/akfamily/akshare)(免费、无 Token)
- **LLM**: 任意 OpenAI 兼容接口(DeepSeek / 通义 / Kimi / GPT 等)
- **模拟盘**: 虚拟资金 ¥1,000,000,遵守 A 股 T+1 / 整手 / 涨跌停 / 佣金印花税规则
- **技术栈**: FastAPI + SQLite + APScheduler / Vue 3 + Element Plus + ECharts

> ⚠️ 本项目仅供学习研究,不构成任何投资建议。模拟盘成交为理想化撮合,与实盘存在差异。

## 决策流程

对股池 + 当前持仓中的每只股票,依次执行:

```
技术分析师 ─┐
基本面分析师 ─┼→ 多头 vs 空头辩论 (2轮) → 交易员决策 → 风控经理审核 → 硬性风控 → 模拟撮合
新闻情绪分析师 ─┘
```

代码层硬性风控(不依赖 LLM):单票 ≤30% 总资产、单次买入 ≤50% 可用资金、总仓位 ≤90%、浮亏 >10% 强制提示止损。

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

1. 打开前端 → 「股池管理」添加自选股(如 `600519`、`300750`,不支持 ST/北交所)
2. 点击右上角「立即运行一轮」,或等待盘中定时调度(默认 10:00 / 11:00 / 13:30 / 14:30,交易日)
3. 「决策记录」查看每轮各 Agent 的完整报告、辩论与最终决策
4. 「仪表盘」查看持仓、收益曲线(vs 沪深300)

## 配置(backend/.env)

| 变量 | 说明 | 默认 |
|---|---|---|
| `LLM_BASE_URL` | OpenAI 兼容接口地址 | `https://api.deepseek.com` |
| `LLM_API_KEY` | API Key | - |
| `LLM_MODEL` | 模型名 | `deepseek-chat` |
| `INITIAL_CASH` | 初始虚拟资金 | `1000000` |
| `SCHEDULE_ENABLED` | 开启盘中定时决策 | `true` |
| `SCHEDULE_TIMES` | 决策时刻(逗号分隔) | `10:00,11:00,13:30,14:30` |
| `DB_PATH` | SQLite 路径(Docker 内为 `/data/stock_ai.db`) | `stock_ai.db` |

## 测试

```bash
cd backend && .venv/bin/pytest tests/ -v
```

覆盖:撮合规则(T+1/整手/涨跌停/费用)、风控硬规则、技术指标计算、Agent 流水线编排与 JSON 解析容错(mock LLM)。

## 说明

- 非交易时段手动触发仍可运行,以最近价格撮合,便于调试。
- 每只股票一轮约消耗 9 次 LLM 调用,注意 token 成本。
- 数据库为 SQLite 单文件(本地开发 `backend/stock_ai.db`,Docker 部署 `./data/stock_ai.db`),删除即全部重置。

## 维护者说明

- CI: `.github/workflows/docker.yml`,push 到 main 自动构建并推送 `latest` 与 `sha-<commit>` 双 tag 到 GHCR。
- 首次 CI 运行后,镜像包默认私有:到 GitHub → Packages → `stock-ai` → Package settings → Change visibility 设为 Public,他人才能免登录拉取。
