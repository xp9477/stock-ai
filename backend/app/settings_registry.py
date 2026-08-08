"""可调配置注册表：唯一真相源（key / 分组 / 类型 / 默认值 / 说明）。

业务代码通过 runtime_settings.get_setting(key) 读取；
默认值在此声明，用户覆盖写入 DB setting_overrides。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from .agents import prompts

SettingType = Literal["int", "float", "bool", "str", "text", "percent", "time", "secret"]
# normal=随时改; confirm=保存前强确认; frozen=仅影响新开账户/重置后（改参不改已有赛季）
DangerLevel = Literal["normal", "confirm", "frozen"]


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
    danger: DangerLevel = "normal"


# 分组元信息（UI Tab 顺序）
GROUPS: list[dict[str, str]] = [
    {"id": "secrets", "label": "密钥", "description": "LLM / 数据源 API（脱敏；DB 覆盖优先于 .env）"},
    {"id": "datasources", "label": "数据源", "description": "扶摇/新浪/腾讯/Tushare/RSS：启停、超时、失败策略与健康状态"},
    {"id": "account", "label": "账户", "description": "初始资金等（冻结项仅影响新开/重置账户）"},
    {"id": "trading", "label": "撮合", "description": "佣金/印花税/过户费（改后影响后续可比性）"},
    {"id": "selector", "label": "选股", "description": "规则初筛阈值、股池与淘汰"},
    {"id": "prompt", "label": "Prompt", "description": "各 Agent 系统提示词"},
    {"id": "risk", "label": "风控", "description": "止盈止损、仓位硬顶"},
    {"id": "factor", "label": "因子", "description": "S3 因子窗口、中性化与 Top N"},
    {"id": "models", "label": "参赛账户", "description": "摘要只读 · 管理请到参赛账户页"},
    {"id": "schedule", "label": "调度", "description": "定时决策 / 选股 / 监控"},
    {"id": "race", "label": "赛马", "description": "样本门槛（可验证 edge）"},
    {"id": "logs", "label": "日志", "description": "全局日志保留与级别"},
    {"id": "debug", "label": "调试", "description": "决策流水线调试开关"},
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

    # ---------- 数据源（固定角色；启停/超时/失败策略）----------
    # fail_policy: fallback=尝试下一源; hard=立即失败; skip=跳过该能力
    SettingDef(
        key="datasources.fuyao.enabled",
        group="datasources", type="bool", default=True,
        label="扶摇 · 启用",
        description="主行情/财务源（沪深300+中证500 成分选股快照）",
    ),
    SettingDef(
        key="datasources.fuyao.timeout_sec",
        group="datasources", type="int", default=30,
        label="扶摇 · 超时", unit="秒",
        description="单次 HTTP 请求超时",
        min_value=3, max_value=120,
    ),
    SettingDef(
        key="datasources.fuyao.fail_policy",
        group="datasources", type="str", default="fallback",
        label="扶摇 · 失败策略",
        description="fallback=降级新浪等兜底; hard=硬失败; skip=跳过",
    ),
    SettingDef(
        key="datasources.sina.enabled",
        group="datasources", type="bool", default=True,
        label="新浪 · 启用",
        description="选股全市场快照兜底 + 指数日线/交易日历（AKShare，无 Key）",
    ),
    SettingDef(
        key="datasources.sina.timeout_sec",
        group="datasources", type="int", default=25,
        label="新浪 · 超时", unit="秒",
        description="全市场接口易慢/被掐，建议 15–30 秒",
        min_value=5, max_value=120,
    ),
    SettingDef(
        key="datasources.sina.fail_policy",
        group="datasources", type="str", default="hard",
        label="新浪 · 失败策略",
        description="作为选股兜底时：hard=双源皆失败则报错; skip=返回空候选",
    ),
    SettingDef(
        key="datasources.tencent.enabled",
        group="datasources", type="bool", default=True,
        label="腾讯 · 启用",
        description="实时行情与日 K（qt.gtimg.cn）",
    ),
    SettingDef(
        key="datasources.tencent.timeout_sec",
        group="datasources", type="int", default=10,
        label="腾讯 · 超时", unit="秒",
        min_value=2, max_value=60,
    ),
    SettingDef(
        key="datasources.tencent.fail_policy",
        group="datasources", type="str", default="hard",
        label="腾讯 · 失败策略",
        description="实时行情失败时 hard=抛错/空报价; skip=静默空",
    ),
    SettingDef(
        key="datasources.tushare.enabled",
        group="datasources", type="bool", default=False,
        label="Tushare · 启用",
        description="可选备份数据源（需 Token；当前主路径默认不用）",
    ),
    SettingDef(
        key="datasources.tushare.timeout_sec",
        group="datasources", type="int", default=30,
        label="Tushare · 超时", unit="秒",
        min_value=5, max_value=120,
    ),
    SettingDef(
        key="datasources.tushare.fail_policy",
        group="datasources", type="str", default="skip",
        label="Tushare · 失败策略",
    ),
    SettingDef(
        key="datasources.rss.enabled",
        group="datasources", type="bool", default=True,
        label="新闻 RSS · 启用",
        description="公开 RSS 资讯（源列表在代码 backend/app/data/news_rss.py 的 RSS_FEEDS；此处管启停/超时）。决策流水线与 factsheet 共用。",
    ),
    SettingDef(
        key="datasources.rss.timeout_sec",
        group="datasources", type="int", default=12,
        label="新闻 RSS · 超时", unit="秒",
        min_value=3, max_value=60,
    ),
    SettingDef(
        key="datasources.rss.fail_policy",
        group="datasources", type="str", default="skip",
        label="新闻 RSS · 失败策略",
        description="skip=无新闻继续决策; hard=新闻失败则该票分析失败。源 URL 暂不可在 UI 编辑。",
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

    # ---------- 账户 / 撮合（P1 全收口）----------
    SettingDef(
        key="account.initial_cash",
        group="account", type="float", default=1_000_000.0,
        label="初始资金", unit="元",
        description="仅对新创建账户 /「重置全部账户」生效；已有账户余额不变",
        min_value=10_000, max_value=1e9,
        danger="frozen",
    ),
    SettingDef(
        key="trading.commission_rate",
        group="trading", type="float", default=0.00025,
        label="佣金费率",
        description="双边佣金比例（如 0.00025=万一）",
        min_value=0.0, max_value=0.01,
        danger="confirm",
    ),
    SettingDef(
        key="trading.commission_min",
        group="trading", type="float", default=5.0,
        label="佣金最低", unit="元",
        min_value=0.0, max_value=100.0,
        danger="confirm",
    ),
    SettingDef(
        key="trading.stamp_tax_rate",
        group="trading", type="float", default=0.0005,
        label="印花税（卖出）",
        description="仅卖出收取",
        min_value=0.0, max_value=0.01,
        danger="confirm",
    ),
    SettingDef(
        key="trading.transfer_fee_rate",
        group="trading", type="float", default=0.00001,
        label="过户费",
        min_value=0.0, max_value=0.001,
        danger="confirm",
    ),

    # ---------- 调度 ----------
    SettingDef(
        key="schedule.enabled",
        group="schedule", type="bool", default=True,
        label="启用定时调度",
        description="关闭后不跑定时决策/选股/监控（可手动触发）",
        requires_scheduler_reload=True,
    ),
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
        danger="confirm",
    ),
    SettingDef(
        key="race.max_live_rule_arms",
        group="race", type="int", default=10,
        label="可竞赛规则臂上限",
        description="不含锚（池内等权）；含 S2 与研究晋升。满额需先退役再晋升",
        min_value=2, max_value=50,
        danger="confirm",
    ),

    # ---------- 日志 ----------
    SettingDef(
        key="logs.retention_days",
        group="logs", type="int", default=30,
        label="日志保留天数",
        description="全局系统日志超过该天数自动清理（默认 30）",
        min_value=1, max_value=365,
    ),
    SettingDef(
        key="logs.min_level",
        group="logs", type="str", default="INFO",
        label="落库最低级别",
        description="DEBUG / INFO / WARNING / ERROR（密钥永不入正文）",
    ),

    # ---------- 调试 ----------
    SettingDef(
        key="debug.pipeline_verbose",
        group="debug", type="bool", default=False,
        label="流水线详细进度",
        description="日志与进度消息更细（Agent 级）；不影响是否落库",
    ),
    SettingDef(
        key="debug.show_agent_io_default",
        group="debug", type="bool", default=False,
        label="决策页默认展开输入摘要",
        description="Run 详情「调试」开关的默认值；密钥永不入日志",
    ),
    SettingDef(
        key="research.inject_promoted_to_llm",
        group="debug", type="bool", default=False,
        label="研究晋升规格注入 LLM",
        description="开启后决策流水线向交易员提示注入已晋升研究策略摘要（可参考不强制跟单）",
    ),
    SettingDef(
        key="research.grid_max_combos",
        group="debug", type="int", default=48,
        label="网格最大组合数",
        description="规则库批量回测笛卡尔积上限",
        min_value=4, max_value=120,
    ),
]

# 风控阈值：改前强确认
def _patch_danger() -> None:
    global _DEFS
    patched = []
    for d in _DEFS:
        if d.group == "risk" and d.danger == "normal" and d.key.startswith("risk."):
            if any(x in d.key for x in (
                "deep_loss", "max_position", "max_total", "max_buy",
                "take_profit", "stop_loss",
            )):
                patched.append(SettingDef(
                    key=d.key, group=d.group, type=d.type, default=d.default,
                    label=d.label, description=d.description,
                    min_value=d.min_value, max_value=d.max_value, unit=d.unit,
                    editable=d.editable,
                    requires_scheduler_reload=d.requires_scheduler_reload,
                    secret=d.secret, danger="confirm",
                ))
                continue
        if d.group == "race" and d.danger == "normal":
            patched.append(SettingDef(
                key=d.key, group=d.group, type=d.type, default=d.default,
                label=d.label, description=d.description,
                min_value=d.min_value, max_value=d.max_value, unit=d.unit,
                editable=d.editable,
                requires_scheduler_reload=d.requires_scheduler_reload,
                secret=d.secret, danger="confirm",
            ))
            continue
        if d.group == "factor" and d.key == "factor.top_n" and d.danger == "normal":
            patched.append(SettingDef(
                key=d.key, group=d.group, type=d.type, default=d.default,
                label=d.label, description=d.description,
                min_value=d.min_value, max_value=d.max_value, unit=d.unit,
                editable=d.editable,
                requires_scheduler_reload=d.requires_scheduler_reload,
                secret=d.secret, danger="confirm",
            ))
            continue
        patched.append(d)
    _DEFS = patched


_patch_danger()


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
