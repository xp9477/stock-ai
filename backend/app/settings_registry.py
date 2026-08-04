"""可调配置注册表：唯一真相源（key / 分组 / 类型 / 默认值 / 说明）。

业务代码通过 runtime_settings.get_setting(key) 读取；
默认值在此声明，用户覆盖写入 DB setting_overrides。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from .agents import prompts

SettingType = Literal["int", "float", "bool", "str", "text", "percent", "time", "secret"]


@dataclass(frozen=True)
class SettingDef:
    key: str
    group: str
    type: SettingType
    default: Any
    label: str
    description: str = ""
    min_value: float | None = None
    max_value: float | None = None
    unit: str = ""
    editable: bool = True
    # 修改后是否需要重载 APScheduler
    requires_scheduler_reload: bool = False
    # 敏感字段：API 只返回脱敏，空写=不修改
    secret: bool = False


# 分组元信息（UI Tab 顺序）
GROUPS: list[dict[str, str]] = [
    {"id": "secrets", "label": "密钥", "description": "LLM / 数据源 API（脱敏；DB 覆盖优先于 .env）"},
    {"id": "selector", "label": "选股", "description": "规则初筛阈值、股池与淘汰"},
    {"id": "prompt", "label": "Prompt", "description": "各 Agent 系统提示词"},
    {"id": "risk", "label": "风控", "description": "止盈止损、仓位硬顶"},
    {"id": "factor", "label": "因子", "description": "S3 因子窗口、中性化与 Top N"},
    {"id": "models", "label": "参赛账户", "description": "摘要只读 · 管理请到参赛账户页"},
    {"id": "schedule", "label": "调度", "description": "定时决策 / 选股 / 监控"},
    {"id": "race", "label": "赛马", "description": "样本门槛（可验证 edge）"},
]

_DEFS: list[SettingDef] = [
    # ---------- 密钥 / 数据源 ----------
    SettingDef(
        key="secrets.llm_base_url",
        group="secrets", type="str", default="https://api.deepseek.com",
        label="LLM Base URL",
        description="OpenAI 兼容端点（CPA / DeepSeek / 中转均可）",
    ),
    SettingDef(
        key="secrets.llm_api_key",
        group="secrets", type="secret", default="",
        label="LLM API Key",
        description="所有参赛 LLM 共用；留空保存=不修改。恢复默认后回退 .env",
        secret=True,
    ),
    SettingDef(
        key="secrets.llm_temperature",
        group="secrets", type="float", default=0.7,
        label="LLM Temperature",
        description="生成随机性，0–2",
        min_value=0.0, max_value=2.0,
    ),
    SettingDef(
        key="secrets.fuyao_api_key",
        group="secrets", type="secret", default="",
        label="扶摇 API Key",
        description="同花顺扶摇行情/财务主源（X-api-key）",
        secret=True,
    ),
    SettingDef(
        key="secrets.tushare_token",
        group="secrets", type="secret", default="",
        label="Tushare Token",
        description="可选备份数据源，默认不用",
        secret=True,
    ),

    # ---------- 选股 ----------
    SettingDef(
        key="selector.pool_max",
        group="selector", type="int", default=30,
        label="股池上限",
        description="共享股池最大只数（含手动 + AI 自动）",
        min_value=1, max_value=100,
    ),
    SettingDef(
        key="selector.miss_limit",
        group="selector", type="int", default=3,
        label="连续未看好淘汰次数",
        description="AI 自动股连续 N 次不在 keep/picks 且无持仓时移出",
        min_value=1, max_value=30,
    ),
    SettingDef(
        key="selector.screen.min_price",
        group="selector", type="float", default=3.0,
        label="最低现价", unit="元",
        description="初筛价格下限",
        min_value=0.1, max_value=50,
    ),
    SettingDef(
        key="selector.screen.max_price",
        group="selector", type="float", default=100.0,
        label="最高现价", unit="元",
        description="初筛价格上限（避开一手超仓的超高价股）",
        min_value=10, max_value=5000,
    ),
    SettingDef(
        key="selector.screen.min_turnover_yi",
        group="selector", type="float", default=2.0,
        label="最低成交额", unit="亿",
        description="当日成交额下限，过滤流动性不足标的",
        min_value=0.1, max_value=100,
    ),
    SettingDef(
        key="selector.screen.min_pct",
        group="selector", type="float", default=-3.0,
        label="涨跌幅下限", unit="%",
        description="避免接飞刀",
        min_value=-10, max_value=0,
    ),
    SettingDef(
        key="selector.screen.max_pct",
        group="selector", type="float", default=7.0,
        label="涨跌幅上限", unit="%",
        description="避免追涨停附近",
        min_value=0, max_value=20,
    ),
    SettingDef(
        key="selector.screen.top_n",
        group="selector", type="int", default=30,
        label="候选池 Top N",
        description="规则初筛后按成交额取前 N 交给 LLM",
        min_value=5, max_value=100,
    ),

    # ---------- Prompt（v0 先开放 SELECTOR；其余注册便于后续扩展）----------
    SettingDef(
        key="prompt.selector",
        group="prompt", type="text", default=prompts.SELECTOR,
        label="选股 Agent (SELECTOR)",
        description="AI 选股 system prompt；改后下次选股立即生效",
    ),
    SettingDef(
        key="prompt.technical",
        group="prompt", type="text", default=prompts.TECHNICAL,
        label="技术分析 (TECHNICAL)",
        description="决策流水线 · 技术面分析师",
        editable=True,
    ),
    SettingDef(
        key="prompt.fundamental",
        group="prompt", type="text", default=prompts.FUNDAMENTAL,
        label="基本面 (FUNDAMENTAL)",
        description="决策流水线 · 基本面分析师",
        editable=True,
    ),
    SettingDef(
        key="prompt.news",
        group="prompt", type="text", default=prompts.NEWS,
        label="新闻情绪 (NEWS)",
        description="决策流水线 · 新闻分析师",
        editable=True,
    ),
    SettingDef(
        key="prompt.bull",
        group="prompt", type="text", default=prompts.BULL,
        label="多头 (BULL)",
        description="决策流水线 · 多头研究员",
        editable=True,
    ),
    SettingDef(
        key="prompt.bear",
        group="prompt", type="text", default=prompts.BEAR,
        label="空头 (BEAR)",
        description="决策流水线 · 空头研究员",
        editable=True,
    ),
    SettingDef(
        key="prompt.trader",
        group="prompt", type="text", default=prompts.TRADER,
        label="交易员 (TRADER)",
        description="决策流水线 · 综合决策",
        editable=True,
    ),
    SettingDef(
        key="prompt.risk",
        group="prompt", type="text", default=prompts.RISK,
        label="风控审核 (RISK)",
        description="决策流水线 · 风控经理",
        editable=True,
    ),
    SettingDef(
        key="prompt.market",
        group="prompt", type="text", default=prompts.MARKET,
        label="大盘环境 (MARKET)",
        description="决策流水线 · 市场环境分析师",
        editable=True,
    ),
    SettingDef(
        key="prompt.reflect",
        group="prompt", type="text", default=prompts.REFLECT,
        label="复盘 (REFLECT)",
        description="决策流水线 · 交易复盘",
        editable=True,
    ),
    SettingDef(
        key="prompt.review",
        group="prompt", type="text", default=prompts.REVIEW,
        label="持仓复审 (REVIEW)",
        description="盘中监控触发后的复审决策",
        editable=True,
    ),

    # ---------- 风控 ----------
    SettingDef(
        key="risk.take_profit_review_pct",
        group="risk", type="percent", default=0.15,
        label="止盈警戒线",
        description="浮盈达到后触发浅线告警/复审",
        min_value=0.01, max_value=1.0,
    ),
    SettingDef(
        key="risk.stop_loss_review_pct",
        group="risk", type="percent", default=-0.08,
        label="止损警戒线",
        description="浮亏达到后触发浅线告警/复审（负数）",
        min_value=-0.5, max_value=-0.01,
    ),
    SettingDef(
        key="risk.deep_loss_pct",
        group="risk", type="percent", default=-0.15,
        label="深度亏损线",
        description="达到后可强制砍仓",
        min_value=-0.8, max_value=-0.05,
    ),
    SettingDef(
        key="risk.deep_loss_auto_execute",
        group="risk", type="bool", default=True,
        label="深亏自动强平",
        description="触发深度亏损时是否不经 LLM 直接卖出",
    ),
    SettingDef(
        key="risk.shallow_line_alert_only",
        group="risk", type="bool", default=True,
        label="浅线仅告警",
        description="止盈/止损警戒线只记事件，不自动卖",
    ),
    SettingDef(
        key="risk.max_position_pct",
        group="risk", type="percent", default=0.30,
        label="单票仓位上限",
        description="单票市值 / 总资产",
        min_value=0.05, max_value=1.0,
    ),
    SettingDef(
        key="risk.max_buy_cash_pct",
        group="risk", type="percent", default=0.50,
        label="单次买入现金上限",
        description="单次买入不超过可用资金比例",
        min_value=0.05, max_value=1.0,
    ),
    SettingDef(
        key="risk.max_total_position_pct",
        group="risk", type="percent", default=0.90,
        label="总仓位上限",
        description="持仓市值 / 总资产",
        min_value=0.1, max_value=1.0,
    ),
    SettingDef(
        key="risk.stop_loss_alert_pct",
        group="risk", type="percent", default=-0.10,
        label="决策侧止损提示线",
        description="流水线内提示交易员评估止损",
        min_value=-0.5, max_value=-0.01,
    ),

    # ---------- 因子（S3）----------
    SettingDef(
        key="factor.top_n",
        group="factor", type="int", default=10,
        label="持仓只数 Top N",
        description="规则组从池内按综合分取前 N 等权",
        min_value=1, max_value=30,
    ),
    SettingDef(
        key="factor.lookback_short",
        group="factor", type="int", default=5,
        label="短动量窗口", unit="日",
        min_value=2, max_value=60,
    ),
    SettingDef(
        key="factor.lookback_mid",
        group="factor", type="int", default=20,
        label="中动量窗口", unit="日",
        min_value=5, max_value=120,
    ),
    SettingDef(
        key="factor.lookback_rev",
        group="factor", type="int", default=20,
        label="反转窗口", unit="日",
        description="S3 rev_1m：近窗收益取负（均值回归）",
        min_value=5, max_value=60,
    ),
    SettingDef(
        key="factor.vol_window",
        group="factor", type="int", default=20,
        label="波动率窗口", unit="日",
        min_value=5, max_value=120,
    ),
    SettingDef(
        key="factor.turnover_window",
        group="factor", type="int", default=20,
        label="换手窗口", unit="日",
        description="S3 low_turn：相对成交活跃度窗口",
        min_value=5, max_value=60,
    ),
    SettingDef(
        key="factor.neutralize_size",
        group="factor", type="bool", default=True,
        label="市值/规模中性",
        description="因子对 size_proxy 回归取残差，降低小票暴露",
    ),
    SettingDef(
        key="factor.neutralize_board",
        group="factor", type="bool", default=True,
        label="板块中性",
        description="沪/深/创业/科创伪行业哑变量中性化",
    ),

    # ---------- 调度 ----------
    SettingDef(
        key="schedule.daily_decision_time",
        group="schedule", type="time", default="14:35",
        label="每日 AI 决策时间",
        description="交易日 cron，格式 HH:MM",
        requires_scheduler_reload=True,
    ),
    SettingDef(
        key="schedule.stock_select_enabled",
        group="schedule", type="bool", default=True,
        label="自动选股开关",
        requires_scheduler_reload=True,
    ),
    SettingDef(
        key="schedule.stock_select_time",
        group="schedule", type="time", default="14:05",
        label="自动选股时间",
        description="交易日 cron，格式 HH:MM",
        requires_scheduler_reload=True,
    ),
    SettingDef(
        key="schedule.monitor_interval_minutes",
        group="schedule", type="int", default=15,
        label="盘中监控间隔", unit="分钟",
        min_value=5, max_value=60,
        requires_scheduler_reload=True,
    ),

    # ---------- 赛马 ----------
    SettingDef(
        key="race.min_trade_days",
        group="race", type="int", default=60,
        label="最少交易日",
        description="样本门槛：交易日天数",
        min_value=1, max_value=365,
    ),
    SettingDef(
        key="race.min_closed_trades",
        group="race", type="int", default=100,
        label="最少平仓笔数",
        description="样本门槛：已闭环成交笔数",
        min_value=1, max_value=10000,
    ),
]

REGISTRY: dict[str, SettingDef] = {d.key: d for d in _DEFS}


def list_defs(group: str | None = None) -> list[SettingDef]:
    items = list(_DEFS)
    if group:
        items = [d for d in items if d.group == group]
    return items


def get_def(key: str) -> SettingDef:
    if key not in REGISTRY:
        raise KeyError(f"未知配置项: {key}")
    return REGISTRY[key]
