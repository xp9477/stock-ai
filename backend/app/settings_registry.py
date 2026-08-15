"""可调配置注册表：唯一真相源（key / 分组 / 类型 / 默认值 / 说明）。

业务代码通过 runtime_settings.get_setting(key) 读取；
默认值在此声明，用户覆盖写入 DB setting_overrides。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from .agents import prompts

SettingType = Literal["int", "float", "bool", "str", "text", "percent", "time", "secret"]
# normal=随时改; confirm=保存前强确认; frozen=仅影响新的证据纪元
DangerLevel = Literal["normal", "confirm", "frozen"]
EvidenceRole = Literal["contract", "provisional", "invariant", "operational"]


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
    # contract=用户资金契约；provisional=必须接受历史/前瞻验证；
    # invariant=工程正确性；operational=密钥、调度等运维项。
    evidence_role: EvidenceRole = "operational"
    # 前端数字框：费率这类小小数必须显式给出，不能默认两位。
    step: float | None = None
    precision: int | None = None


# 分组元信息（UI Tab 顺序）
GROUPS: list[dict[str, str]] = [
    {"id": "secrets", "label": "密钥", "description": "LLM / 数据源 API（脱敏；DB 覆盖优先于 .env）"},
    {"id": "datasources", "label": "数据源", "description": "扶摇/新浪/Tushare/RSS：启停、超时、失败策略与健康状态"},
    {"id": "notifications", "label": "通知", "description": "Bark 人工介入提醒：候选计划、风险复审与运行异常"},
    {"id": "account", "label": "账户", "description": "官方策略初始资金契约（只读）"},
    {"id": "capital", "label": "资金契约", "description": "用户明确授权的资金与损失边界；不参与收益调参"},
    {"id": "signal", "label": "信号策略", "description": "候选参数，必须版本化并接受历史/前瞻验证"},
    {"id": "execution", "label": "执行契约", "description": "人工确认与成交安全不变量"},
    {"id": "trading", "label": "撮合", "description": "佣金/印花税/过户费（改后影响后续可比性）"},
    {"id": "selector", "label": "选股", "description": "规则初筛阈值、股池与淘汰"},
    {"id": "prompt", "label": "Prompt", "description": "各 Agent 系统提示词"},
    {"id": "risk", "label": "风控", "description": "止盈止损、仓位硬顶"},
    {"id": "factor", "label": "因子", "description": "S3 因子窗口、中性化与 Top N"},
    {"id": "models", "label": "模型与策略", "description": "LLM 顾问与唯一官方 ensemble 策略"},
    {"id": "schedule", "label": "调度", "description": "定时决策 / 选股 / 监控"},
    {"id": "validation", "label": "证据门槛", "description": "历史/前瞻样本的最低验证要求"},
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
        description="所有判断 LLM 共用；留空保存=不修改。恢复默认后回退 .env",
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

    # ---------- 人工介入通知 ----------
    SettingDef(
        key="notifications.bark.enabled",
        group="notifications", type="bool", default=False,
        label="启用 Bark 通知",
        description="仅通知需要人工介入的候选计划、风险复审和运行异常；普通观望不通知",
    ),
    SettingDef(
        key="notifications.bark.server_url",
        group="notifications", type="str", default="https://api.day.app",
        label="Bark Server",
        description="官方服务或自建服务根地址；系统使用 POST /push",
    ),
    SettingDef(
        key="notifications.bark.device_key",
        group="notifications", type="secret", default="",
        label="Bark Device Key",
        description="Bark App 测试 URL 中的设备 Key；API 永不回传明文",
        secret=True,
    ),
    SettingDef(
        key="notifications.bark.open_url",
        group="notifications", type="str", default="",
        label="手机可访问的系统地址",
        description="例如 http://192.168.1.20:18000；留空则通知不附点击跳转",
    ),
    SettingDef(
        key="notifications.bark.group",
        group="notifications", type="str", default="stock-ai",
        label="通知分组",
        description="Bark 历史消息中的分组名称",
    ),
    SettingDef(
        key="notifications.bark.timeout_sec",
        group="notifications", type="int", default=8,
        label="推送超时", unit="秒",
        description="推送失败只记日志，不影响决策或持仓",
        min_value=2, max_value=30,
    ),
    SettingDef(
        key="notifications.bark.notify_candidates",
        group="notifications", type="bool", default=True,
        label="候选计划待审批",
        description="出现买入/卖出候选计划时发送时效性通知",
    ),
    SettingDef(
        key="notifications.bark.notify_risk_reviews",
        group="notifications", type="bool", default=True,
        label="持仓风险与复审",
        description="止盈止损警戒、深亏或 LLM 建议卖出时通知",
    ),
    SettingDef(
        key="notifications.bark.notify_failures",
        group="notifications", type="bool", default=True,
        label="运行失败与降级",
        description="决策/选股失败，或模型与数据降级时通知",
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
        key="prompt.independent_judgment",
        group="prompt", type="text", default=prompts.INDEPENDENT_JUDGMENT,
        label="独立判断模型",
        description="所有模型读取同一冻结事实；不得看到持仓、成本或盈亏",
        evidence_role="provisional",
    ),
    SettingDef(
        key="prompt.final_trader",
        group="prompt", type="text", default=prompts.FINAL_TRADER,
        label="最终交易员",
        description="读取独立判断与授权账户状态，输出条件计划而非订单",
        evidence_role="provisional",
    ),
    SettingDef(
        key="prompt.risk_review",
        group="prompt", type="text", default=prompts.RISK_REVIEW,
        label="风险审查",
        description="读取完整账户回撤与损失预算，只审查建议、不成交",
        evidence_role="provisional",
    ),
    SettingDef(
        key="prompt.selector",
        group="prompt", type="text", default=prompts.SELECTOR,
        label="选股 Agent (SELECTOR)",
        description="AI 选股 system prompt；改后下次选股立即生效",
        evidence_role="provisional",
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
        label="严重复审线",
        description="达到后产生高优先级复审；不会自动卖出",
        min_value=-0.8, max_value=-0.05,
    ),
    SettingDef(
        key="risk.deep_loss_auto_execute",
        group="risk", type="bool", default=False,
        label="深亏自动强平（已禁用）",
        description="保留旧配置兼容；交易链路永远忽略此值，任何卖出均须人工确认",
        editable=False,
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
        group="risk", type="percent", default=0.80,
        label="总仓位上限",
        description="持仓市值 / 总资产",
        min_value=0.1, max_value=1.0,
        evidence_role="provisional",
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

    # ---------- 资金契约 / 信号策略 / 执行契约 ----------
    SettingDef(
        key="capital.authorized_capital",
        group="capital", type="float", default=100_000.0,
        label="Canary 授权资金", unit="元",
        description="策略最多可管理的资金；个人其余本金不得注入模型上下文",
        min_value=10_000, max_value=400_000,
        editable=False, danger="frozen", evidence_role="contract",
    ),
    SettingDef(
        key="capital.max_stock_exposure",
        group="capital", type="float", default=80_000.0,
        label="股票敞口上限", unit="元",
        description="所有股票持仓市值合计硬上限；不会因为 LLM 建议而放宽",
        min_value=0, max_value=400_000,
        editable=False, danger="confirm", evidence_role="contract",
    ),
    SettingDef(
        key="capital.drawdown_alert_1",
        group="capital", type="float", default=5_000.0,
        label="一级回撤告警", unit="元",
        description="只产生告警，不自动减仓",
        min_value=0, max_value=100_000,
        editable=False, danger="confirm", evidence_role="contract",
    ),
    SettingDef(
        key="capital.drawdown_alert_2",
        group="capital", type="float", default=10_000.0,
        label="二级回撤告警", unit="元",
        description="只产生告警，不自动减仓",
        min_value=0, max_value=200_000,
        editable=False, danger="confirm", evidence_role="contract",
    ),
    SettingDef(
        key="capital.canary_stop_drawdown",
        group="capital", type="float", default=15_000.0,
        label="Canary 终止回撤", unit="元",
        description="达到后禁止新增风险，但不自动清仓；仍允许人工确认卖出",
        min_value=1_000, max_value=300_000,
        editable=False, danger="frozen", evidence_role="contract",
    ),
    SettingDef(
        key="signal.max_positions",
        group="signal", type="int", default=3,
        label="最大持仓只数（候选）",
        description="provisional 参数；必须通过确定性历史验证后才能晋升",
        min_value=1, max_value=20,
        danger="confirm", evidence_role="provisional",
    ),
    SettingDef(
        key="signal.entry_top_pct",
        group="signal", type="percent", default=0.20,
        label="入场候选因子分位",
        description="综合因子排名进入前多少才允许新建仓；provisional，需回测晋升",
        min_value=0.05, max_value=0.50,
        danger="confirm", evidence_role="provisional",
    ),
    SettingDef(
        key="signal.entry_watch_pct",
        group="signal", type="percent", default=0.35,
        label="观察名单因子分位",
        description="接近入场门禁但确认不足时进入观察层；不得直接生成新建仓计划",
        min_value=0.10, max_value=0.70,
        evidence_role="provisional",
    ),
    SettingDef(
        key="signal.entry_min_confirmations",
        group="signal", type="int", default=3,
        label="最少趋势确认数",
        description="短/中期动量、站上 MA20、MACD 四项中至少满足的数量",
        min_value=1, max_value=4,
        danger="confirm", evidence_role="provisional",
    ),
    SettingDef(
        key="signal.entry_max_rsi",
        group="signal", type="float", default=75.0,
        label="入场 RSI 过热线",
        description="达到该 RSI14 时禁止新建仓，避免追入短期过热标的",
        min_value=55.0, max_value=95.0,
        danger="confirm", evidence_role="provisional",
    ),
    SettingDef(
        key="signal.gap_lookback_days",
        group="signal", type="int", default=60,
        label="开盘缺口回看窗口", unit="交易日",
        min_value=20, max_value=240,
        evidence_role="provisional",
    ),
    SettingDef(
        key="signal.gap_percentile",
        group="signal", type="float", default=0.95,
        label="动态缺口异常分位",
        min_value=0.80, max_value=0.999,
        evidence_role="provisional",
    ),
    SettingDef(
        key="signal.gap_min_samples",
        group="signal", type="int", default=40,
        label="缺口门禁最少样本", unit="交易日",
        min_value=10, max_value=200,
        evidence_role="provisional",
    ),
    SettingDef(
        key="signal.hard_price_deviation_pct",
        group="signal", type="percent", default=0.05,
        label="隔夜信号绝对失效线（候选）",
        description="相对分析参考价达到该偏离时旧计划失效；不是止损线",
        min_value=0.01, max_value=0.20,
        danger="confirm", evidence_role="provisional",
    ),
    SettingDef(
        key="signal.default_valid_until",
        group="signal", type="time", default="10:30",
        label="隔夜计划默认失效时间",
        description="仅作 LLM 未给出更早期限时的硬上限",
        evidence_role="provisional",
    ),
    SettingDef(
        key="execution.require_manual_confirmation",
        group="execution", type="bool", default=False,
        label="逐笔人工确认",
        description="关闭后，候选计划在信息/价格/资金门禁通过时自动生成票据；模拟盘可再自动成交",
        evidence_role="operational",
    ),
    SettingDef(
        key="execution.auto_fill_tickets",
        group="execution", type="bool", default=True,
        label="模拟盘自动成交",
        description="票据生成后向 EMT 模拟盘报单，成交后用快照回写本系统持仓。无 EMT 时才用本地撮合。",
        evidence_role="operational",
    ),
    SettingDef(
        key="execution.require_human_information_check",
        group="execution", type="bool", default=False,
        label="人工核对正式公告",
        description="关闭后不再要求勾选「已核对官方公告」；新闻指纹变化仍会阻断旧计划",
        evidence_role="operational",
    ),
    SettingDef(
        key="execution.max_quote_age_seconds",
        group="execution", type="int", default=60,
        label="执行报价最大年龄", unit="秒",
        description="审批瞬间的报价超过该年龄即 fail closed",
        min_value=5, max_value=300,
        danger="confirm", evidence_role="invariant",
    ),

    # ---------- 账户 / 撮合（P1 全收口）----------
    SettingDef(
        key="account.initial_cash",
        group="account", type="float", default=100_000.0,
        label="初始资金", unit="元",
        description="官方 ensemble 策略账户的资金契约；清库式账户重置已禁用",
        min_value=10_000, max_value=1e9,
        editable=False, danger="frozen",
        evidence_role="contract",
    ),
    SettingDef(
        key="trading.commission_rate",
        group="trading", type="float", default=0.00025,
        label="佣金费率",
        description="双边佣金比例（默认 0.00025=万2.5；万一=0.0001）",
        min_value=0.0, max_value=0.01,
        danger="confirm",
        step=0.00001, precision=6,
    ),
    SettingDef(
        key="trading.commission_min",
        group="trading", type="float", default=5.0,
        label="佣金最低", unit="元",
        min_value=0.0, max_value=100.0,
        danger="confirm",
        step=0.1, precision=2,
    ),
    SettingDef(
        key="trading.stamp_tax_rate",
        group="trading", type="float", default=0.0005,
        label="印花税（卖出）",
        description="仅卖出收取（默认 0.0005=万5）",
        min_value=0.0, max_value=0.01,
        danger="confirm",
        step=0.00001, precision=6,
    ),
    SettingDef(
        key="trading.transfer_fee_rate",
        group="trading", type="float", default=0.00001,
        label="过户费",
        description="成交金额比例（默认 0.00001=万0.1）",
        min_value=0.0, max_value=0.001,
        danger="confirm",
        step=0.00001, precision=6,
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
        key="schedule.morning_decision_time",
        group="schedule", type="time", default="09:35",
        label="上午 AI 决策时间",
        description="集合竞价结束后，用最新价格与隔夜信息生成上午候选计划；绝不直接成交",
        requires_scheduler_reload=True,
    ),
    SettingDef(
        key="schedule.daily_decision_time",
        group="schedule", type="time", default="14:10",
        label="下午 AI 决策时间",
        description="午后用变化后的行情与信息重新生成候选计划；绝不直接成交",
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
        group="schedule", type="time", default="08:50",
        label="自动选股时间",
        description="开盘前准备候选股与当日数据，交易日 cron，格式 HH:MM",
        requires_scheduler_reload=True,
    ),
    SettingDef(
        key="schedule.monitor_interval_minutes",
        group="schedule", type="int", default=5,
        label="盘中监控间隔", unit="分钟",
        min_value=5, max_value=60,
        requires_scheduler_reload=True,
    ),

    # ---------- 验证证据门槛（key 保留 race.* 以兼容历史配置） ----------
    SettingDef(
        key="race.min_trade_days",
        group="validation", type="int", default=60,
        label="最少交易日",
        description="样本门槛：交易日天数",
        min_value=1, max_value=365,
    ),
    SettingDef(
        key="race.min_closed_trades",
        group="validation", type="int", default=100,
        label="最少平仓笔数",
        description="样本门槛：已闭环成交笔数",
        min_value=1, max_value=10000,
        danger="confirm",
    ),
    SettingDef(
        key="race.max_live_rule_arms",
        group="validation", type="int", default=10,
        label="历史规则臂上限（已退役）",
        description="仅保留旧配置兼容；资本化规则臂已退役，不再创建新账户",
        min_value=2, max_value=50,
        editable=False, danger="confirm",
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
        label="不可变研究证据注入 LLM",
        description="开启后只注入能重新通过 experiment/holdout 指纹校验的晋升证据；历史 rule 记录不会进入决策上下文",
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
                    evidence_role=d.evidence_role,
                    step=d.step, precision=d.precision,
                ))
                continue
        if d.group == "validation" and d.danger == "normal":
            patched.append(SettingDef(
                key=d.key, group=d.group, type=d.type, default=d.default,
                label=d.label, description=d.description,
                min_value=d.min_value, max_value=d.max_value, unit=d.unit,
                editable=d.editable,
                requires_scheduler_reload=d.requires_scheduler_reload,
                secret=d.secret, danger="confirm",
                evidence_role=d.evidence_role,
                step=d.step, precision=d.precision,
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
                evidence_role=d.evidence_role,
                step=d.step, precision=d.precision,
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
