from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
    inspect as sa_inspect,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def now() -> datetime:
    return datetime.now()


def utc_now() -> datetime:
    """Timezone-aware timestamp for the trade-plan audit trail."""
    return datetime.now(timezone.utc)


class Model(Base):
    """参赛模型或合议组合,各自持有独立虚拟账户。"""

    __tablename__ = "models"
    __table_args__ = (
        Index(
            "ux_models_one_official_strategy",
            "is_official_strategy",
            unique=True,
            sqlite_where=text("is_official_strategy = 1"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(50), unique=True)
    model_id: Mapped[str] = mapped_column(String(100), default="")  # API model 名; ensemble 为空
    type: Mapped[str] = mapped_column(String(10), default="llm")  # llm / ensemble
    members: Mapped[str] = mapped_column(Text, default="[]")  # ensemble 成员 model 主键 JSON 列表
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    is_official_strategy: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Watchlist(Base):
    __tablename__ = "watchlist"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(10), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(50))
    source: Mapped[str] = mapped_column(String(10), default="manual")  # manual / auto
    miss_count: Mapped[int] = mapped_column(Integer, default=0)  # 连续未被 AI 看好次数
    select_reason: Mapped[str] = mapped_column(Text, default="")  # AI 选入理由
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Account(Base):
    __tablename__ = "account"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    model_pk: Mapped[int] = mapped_column(ForeignKey("models.id"), unique=True, index=True)
    cash: Mapped[float] = mapped_column(Float)
    initial_cash: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Position(Base):
    __tablename__ = "positions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    model_pk: Mapped[int] = mapped_column(ForeignKey("models.id"), index=True)
    code: Mapped[str] = mapped_column(String(10), index=True)
    name: Mapped[str] = mapped_column(String(50))
    total_qty: Mapped[int] = mapped_column(Integer)
    available_qty: Mapped[int] = mapped_column(Integer)  # T+1 可卖数量
    avg_cost: Mapped[float] = mapped_column(Float)
    buy_reason: Mapped[str] = mapped_column(Text, default="")  # 最近一次买入理由,供复审
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    model_pk: Mapped[int] = mapped_column(ForeignKey("models.id"), index=True)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id"), nullable=True)
    code: Mapped[str] = mapped_column(String(10), index=True)
    name: Mapped[str] = mapped_column(String(50))
    side: Mapped[str] = mapped_column(String(4))  # buy / sell
    price: Mapped[float] = mapped_column(Float)
    qty: Mapped[int] = mapped_column(Integer)
    amount: Mapped[float] = mapped_column(Float)
    fee: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(10), default="filled")  # filled / rejected
    reject_reason: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trigger: Mapped[str] = mapped_column(String(10))  # manual / schedule / selector
    status: Mapped[str] = mapped_column(String(10), default="running")  # running / done / failed / cancelled
    error: Mapped[str] = mapped_column(Text, default="")
    # 结构化结果（选股新增/移除、决策买卖计数等），JSON 字符串
    result_json: Mapped[str] = mapped_column(Text, default="")
    started_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class AgentOutput(Base):
    __tablename__ = "agent_outputs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"), index=True)
    model_pk: Mapped[int] = mapped_column(ForeignKey("models.id"), index=True)
    code: Mapped[str] = mapped_column(String(10), index=True)  # 大盘环境/反思用 "MARKET"/"REFLECT"
    agent: Mapped[str] = mapped_column(String(30))
    input_summary: Mapped[str] = mapped_column(Text, default="")
    output: Mapped[str] = mapped_column(Text, default="")
    model_id_snapshot: Mapped[str] = mapped_column(String(100), default="")
    prompt_hash: Mapped[str] = mapped_column(String(64), default="")
    input_hash: Mapped[str] = mapped_column(String(64), default="")
    output_hash: Mapped[str] = mapped_column(String(64), default="")
    config_snapshot_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Decision(Base):
    __tablename__ = "decisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"), index=True)
    model_pk: Mapped[int] = mapped_column(ForeignKey("models.id"), index=True)
    code: Mapped[str] = mapped_column(String(10), index=True)
    name: Mapped[str] = mapped_column(String(50))
    action: Mapped[str] = mapped_column(String(10))  # buy / sell / hold
    target_position_pct: Mapped[float] = mapped_column(Float, default=0.0)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    reason: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class TradePlan(Base):
    """Conditional authorization candidate produced from an analytical decision.

    A plan is deliberately separate from :class:`Order`: creating it must not
    mutate cash, positions, or executions.  Gate checks and a human approval
    can later advance the plan through its lifecycle.
    """

    __tablename__ = "trade_plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    decision_id: Mapped[int] = mapped_column(ForeignKey("decisions.id"), index=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"), index=True)
    model_pk: Mapped[int] = mapped_column(ForeignKey("models.id"), index=True)
    supersedes_plan_id: Mapped[int | None] = mapped_column(
        ForeignKey("trade_plans.id"), nullable=True, index=True)

    code: Mapped[str] = mapped_column(String(10), index=True)
    name: Mapped[str] = mapped_column(String(50), default="")
    side: Mapped[str] = mapped_column(String(8))  # buy / sell
    status: Mapped[str] = mapped_column(String(64), default="candidate", index=True)
    status_reason_code: Mapped[str] = mapped_column(String(64), default="")
    status_reason: Mapped[str] = mapped_column(Text, default="")
    version: Mapped[int] = mapped_column(Integer, default=1)
    lock_version: Mapped[int] = mapped_column(Integer, default=1)
    idempotency_key: Mapped[str | None] = mapped_column(
        String(128), nullable=True, unique=True, index=True)

    target_position_pct: Mapped[float] = mapped_column(Float, default=0.0)
    max_buy_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    reference_price: Mapped[float] = mapped_column(Float)
    reference_price_kind: Mapped[str] = mapped_column(
        String(32), default="official_close")
    reference_price_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    data_cutoff_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    valid_from_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)

    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    thesis: Mapped[str] = mapped_column(Text, default="")
    invalidation_conditions_json: Mapped[str] = mapped_column(Text, default="{}")
    policy_snapshot_json: Mapped[str] = mapped_column(Text, default="{}")
    factsheet_hash: Mapped[str] = mapped_column(String(64), default="")

    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class GateCheck(Base):
    """Immutable evidence for one information, price, or approval gate."""

    __tablename__ = "gate_checks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("trade_plans.id"), index=True)
    plan_version: Mapped[int] = mapped_column(Integer)
    gate_type: Mapped[str] = mapped_column(String(64), index=True)
    outcome: Mapped[str] = mapped_column(String(64), index=True)
    reason_code: Mapped[str] = mapped_column(String(64), default="")
    reason: Mapped[str] = mapped_column(Text, default="")
    idempotency_key: Mapped[str | None] = mapped_column(
        String(128), nullable=True, unique=True, index=True)

    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, index=True)
    coverage_from: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    coverage_to: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    required_sources_json: Mapped[str] = mapped_column(Text, default="[]")
    source_results_json: Mapped[str] = mapped_column(Text, default="{}")

    quote_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    quote_asof: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    opening_gap_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    dynamic_gap_threshold_pct: Mapped[float | None] = mapped_column(
        Float, nullable=True)
    signal_price_deviation_pct: Mapped[float | None] = mapped_column(
        Float, nullable=True)
    metrics_json: Mapped[str] = mapped_column(Text, default="{}")
    new_information_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    input_hash: Mapped[str] = mapped_column(String(64), default="")


class ExecutionIntent(Base):
    """Human-approved order ticket; still not a filled order."""

    __tablename__ = "execution_intents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    plan_id: Mapped[int] = mapped_column(
        ForeignKey("trade_plans.id"), unique=True, index=True)
    status: Mapped[str] = mapped_column(
        String(64), default="ticket_ready", index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    lock_version: Mapped[int] = mapped_column(Integer, default=1)
    idempotency_key: Mapped[str] = mapped_column(
        String(128), unique=True, index=True)

    approved_by: Mapped[str] = mapped_column(String(80), default="local_user")
    approved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now)
    approval_quote_price: Mapped[float] = mapped_column(Float)
    approval_quote_asof: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    authorized_target_position_pct: Mapped[float] = mapped_column(Float, default=0.0)
    authorized_notional: Mapped[float] = mapped_column(Float, default=0.0)
    authorized_qty: Mapped[int] = mapped_column(Integer, default=0)
    estimated_fee: Mapped[float] = mapped_column(Float, default=0.0)
    risk_snapshot_json: Mapped[str] = mapped_column(Text, default="{}")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    order_id: Mapped[int | None] = mapped_column(
        ForeignKey("orders.id"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class ReviewEvent(Base):
    """Non-executing intraday review task for a plan or an open position."""

    __tablename__ = "review_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    plan_id: Mapped[int | None] = mapped_column(
        ForeignKey("trade_plans.id"), nullable=True, index=True)
    position_id: Mapped[int | None] = mapped_column(
        ForeignKey("positions.id"), nullable=True, index=True)
    model_pk: Mapped[int | None] = mapped_column(
        ForeignKey("models.id"), nullable=True, index=True)
    code: Mapped[str] = mapped_column(String(10), index=True)
    name: Mapped[str] = mapped_column(String(50), default="")
    trigger_type: Mapped[str] = mapped_column(String(64), index=True)
    severity: Mapped[str] = mapped_column(String(32), default="warning")
    status: Mapped[str] = mapped_column(String(64), default="open", index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    lock_version: Mapped[int] = mapped_column(Integer, default=1)
    idempotency_key: Mapped[str | None] = mapped_column(
        String(128), nullable=True, unique=True, index=True)

    observed_json: Mapped[str] = mapped_column(Text, default="{}")
    llm_recommendation_json: Mapped[str] = mapped_column(Text, default="{}")
    human_resolution_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)


class CanaryState(Base):
    """Durable state for the user-authorized canary capital contract."""

    __tablename__ = "canary_states"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active','stopped')",
            name="ck_canary_state_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    model_pk: Mapped[int] = mapped_column(
        ForeignKey("models.id"), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)
    high_water: Mapped[float] = mapped_column(Float, default=0.0)
    risk_equity: Mapped[float] = mapped_column(Float, default=0.0)
    drawdown: Mapped[float] = mapped_column(Float, default=0.0)
    alert_level: Mapped[int] = mapped_column(Integer, default=0)
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=now, onupdate=now)


class EquitySnapshot(Base):
    __tablename__ = "equity_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    model_pk: Mapped[int] = mapped_column(ForeignKey("models.id"), index=True)
    total_equity: Mapped[float] = mapped_column(Float)
    cash: Mapped[float] = mapped_column(Float)
    market_value: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Reflection(Base):
    __tablename__ = "reflections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    model_pk: Mapped[int] = mapped_column(ForeignKey("models.id"), index=True)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id"), nullable=True)
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class MonitorEvent(Base):
    __tablename__ = "monitor_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    model_pk: Mapped[int] = mapped_column(ForeignKey("models.id"), index=True)
    code: Mapped[str] = mapped_column(String(10), index=True)
    name: Mapped[str] = mapped_column(String(50), default="")
    pnl_pct: Mapped[float] = mapped_column(Float)
    trigger: Mapped[str] = mapped_column(String(15))  # stop_loss / take_profit / deep_loss
    action: Mapped[str] = mapped_column(String(15))  # review_hold / review_sell / alert
    detail: Mapped[str] = mapped_column(Text, default="")  # 复审推理全文
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class TradeLedger(Base):
    """决策账本：信号 → 成交 → 平仓后回填，用于 100 笔样本与置信度校准。"""

    __tablename__ = "trade_ledger"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    strategy_key: Mapped[str] = mapped_column(String(40), index=True)  # llm:1 / ensemble:3 / rule:s2
    model_pk: Mapped[int | None] = mapped_column(ForeignKey("models.id"), nullable=True, index=True)
    code: Mapped[str] = mapped_column(String(10), index=True)
    name: Mapped[str] = mapped_column(String(50), default="")
    side: Mapped[str] = mapped_column(String(4))  # open / close
    qty: Mapped[int] = mapped_column(Integer, default=0)
    price: Mapped[float] = mapped_column(Float, default=0.0)
    signal_source: Mapped[str] = mapped_column(String(30), default="")  # trader / factor / deep_loss
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    reason: Mapped[str] = mapped_column(Text, default="")
    factsheet_hash: Mapped[str] = mapped_column(String(64), default="")
    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id"), nullable=True)
    # 平仓回填
    is_closed: Mapped[bool] = mapped_column(Boolean, default=False)
    pnl: Mapped[float] = mapped_column(Float, default=0.0)
    pnl_pct: Mapped[float] = mapped_column(Float, default=0.0)
    hold_days: Mapped[int] = mapped_column(Integer, default=0)
    opened_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class PoolVersion(Base):
    """共享股池版本快照，变更打版本号，避免赛中偷换宇宙。"""

    __tablename__ = "pool_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version: Mapped[str] = mapped_column(String(30), unique=True)  # e.g. pool_v1
    codes_json: Mapped[str] = mapped_column(Text, default="[]")
    note: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class SettingOverride(Base):
    """用户覆盖的运行时配置（默认值在 settings_registry）。"""

    __tablename__ = "setting_overrides"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    value: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)


class SystemLog(Base):
    """全局系统日志（可清理；默认保留 30 天）。"""

    __tablename__ = "system_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    level: Mapped[str] = mapped_column(String(10), default="INFO", index=True)
    logger_name: Mapped[str] = mapped_column(String(120), default="", index=True)
    message: Mapped[str] = mapped_column(Text, default="")
    run_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)


class ResearchHypothesis(Base):
    """研究假说：NL → 规格 → 回测 → 晋升/废弃（P3）。

    status: draft | confirmed | backtested | suggested | discarded | promoted | retired
    """

    __tablename__ = "research_hypotheses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(120), default="")
    theory_text: Mapped[str] = mapped_column(Text, default="")
    # 结构化规格 JSON
    spec_json: Mapped[str] = mapped_column(Text, default="{}")
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    # 回测摘要 JSON（metrics + suggestion）
    backtest_json: Mapped[str] = mapped_column(Text, default="")
    suggestion: Mapped[str] = mapped_column(String(40), default="")  # promote | discard | review
    discard_reason: Mapped[str] = mapped_column(String(500), default="")
    # 晋升后的规则账户 model_id，如 res_12
    promoted_model_id: Mapped[str] = mapped_column(String(40), default="", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)


class BacktestExperiment(Base):
    """Immutable experiment specification plus frozen reproducibility evidence."""

    __tablename__ = "backtest_experiments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    hypothesis_id: Mapped[int | None] = mapped_column(
        ForeignKey("research_hypotheses.id"), nullable=True, index=True)
    parent_experiment_id: Mapped[int | None] = mapped_column(
        ForeignKey("backtest_experiments.id"), nullable=True, index=True)
    campaign_key: Mapped[str] = mapped_column(String(64), index=True)
    experiment_key: Mapped[str] = mapped_column(
        String(64), unique=True, index=True)
    spec_json: Mapped[str] = mapped_column(Text)
    spec_fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    code_fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    config_json: Mapped[str] = mapped_column(Text)
    config_fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    data_fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    data_artifact_json: Mapped[str] = mapped_column(Text)
    universe_json: Mapped[str] = mapped_column(Text)
    universe_fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    data_cutoff_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True)
    data_start: Mapped[str] = mapped_column(String(10))
    data_end: Mapped[str] = mapped_column(String(10))
    row_count: Mapped[int] = mapped_column(Integer)
    date_count: Mapped[int] = mapped_column(Integer)
    validation_mode: Mapped[str] = mapped_column(
        String(32), default="development_holdout", index=True)
    development_ratio: Mapped[float] = mapped_column(Float, default=0.8)
    status: Mapped[str] = mapped_column(String(32), default="running", index=True)
    failure_reason: Mapped[str] = mapped_column(Text, default="")
    holdout_opened_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, index=True)
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)


class BacktestExperimentResult(Base):
    """Append-only result for one immutable experiment phase."""

    __tablename__ = "backtest_experiment_results"
    __table_args__ = (
        UniqueConstraint("experiment_id", "phase", name="uq_experiment_phase"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    experiment_id: Mapped[int] = mapped_column(
        ForeignKey("backtest_experiments.id"), index=True)
    phase: Mapped[str] = mapped_column(String(24), index=True)
    result_json: Mapped[str] = mapped_column(Text)
    result_fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, index=True)


class HoldoutSelection(Base):
    """The one experiment allowed to consume a frozen campaign holdout."""

    __tablename__ = "holdout_selections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    campaign_key: Mapped[str] = mapped_column(
        String(64), unique=True, index=True)
    experiment_id: Mapped[int] = mapped_column(
        ForeignKey("backtest_experiments.id"), unique=True, index=True)
    development_result_id: Mapped[int] = mapped_column(
        ForeignKey("backtest_experiment_results.id"), index=True)
    development_result_fingerprint: Mapped[str] = mapped_column(
        String(64), index=True)
    selected_by: Mapped[str] = mapped_column(String(80), default="local_user")
    selected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, index=True)


class HoldoutAccessEvent(Base):
    """Append-only evidence that a sealed holdout result was viewed."""

    __tablename__ = "holdout_access_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    selection_id: Mapped[int] = mapped_column(
        ForeignKey("holdout_selections.id"), index=True)
    experiment_id: Mapped[int] = mapped_column(
        ForeignKey("backtest_experiments.id"), index=True)
    result_id: Mapped[int] = mapped_column(
        ForeignKey("backtest_experiment_results.id"), index=True)
    result_fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    actor: Mapped[str] = mapped_column(String(80), default="local_user")
    purpose: Mapped[str] = mapped_column(String(200), default="final_validation")
    accessed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, index=True)


class RunMarketArtifact(Base):
    """Immutable engine-produced market inputs shared by one analytical Run."""

    __tablename__ = "run_market_artifacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"), unique=True, index=True)
    source_key: Mapped[str] = mapped_column(
        String(80), default="engine_strategy_eligible_quote_v1", index=True)
    data_cutoff_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True)
    analysis_universe_json: Mapped[str] = mapped_column(Text)
    eligible_universe_json: Mapped[str] = mapped_column(Text)
    reference_prices_json: Mapped[str] = mapped_column(Text)
    quote_evidence_json: Mapped[str] = mapped_column(Text)
    artifact_fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, index=True)


class ShadowBenchmarkSnapshot(Base):
    """Frozen, zero-capital equal-weight benchmark for one analytical run."""

    __tablename__ = "shadow_benchmark_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"), unique=True, index=True)
    artifact_id: Mapped[int | None] = mapped_column(
        ForeignKey("run_market_artifacts.id"), unique=True, nullable=True, index=True)
    benchmark_key: Mapped[str] = mapped_column(
        String(80), default="eligible_universe_equal_weight_v1", index=True)
    signal_cutoff_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True)
    scheduled_execution_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True)
    universe_json: Mapped[str] = mapped_column(Text)
    universe_fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    weights_json: Mapped[str] = mapped_column(Text)
    reference_prices_json: Mapped[str] = mapped_column(Text)
    reference_data_fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    idempotency_key: Mapped[str] = mapped_column(
        String(128), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, index=True)


class ShadowBenchmarkMark(Base):
    """Append-only observed mark for a frozen zero-capital benchmark."""

    __tablename__ = "shadow_benchmark_marks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(
        ForeignKey("shadow_benchmark_snapshots.id"), index=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    observed_prices_json: Mapped[str] = mapped_column(Text)
    data_fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    benchmark_return_pct: Mapped[float] = mapped_column(Float)
    idempotency_key: Mapped[str] = mapped_column(
        String(128), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, index=True)


_EXPERIMENT_PROVENANCE_FIELDS = (
    "hypothesis_id",
    "parent_experiment_id",
    "campaign_key",
    "experiment_key",
    "spec_json",
    "spec_fingerprint",
    "code_fingerprint",
    "config_json",
    "config_fingerprint",
    "data_fingerprint",
    "data_artifact_json",
    "universe_json",
    "universe_fingerprint",
    "data_cutoff_at",
    "data_start",
    "data_end",
    "row_count",
    "date_count",
    "validation_mode",
    "development_ratio",
)

_EXECUTION_INTENT_AUTHORIZATION_FIELDS = (
    "plan_id",
    "idempotency_key",
    "approved_by",
    "approved_at",
    "approval_quote_price",
    "approval_quote_asof",
    "authorized_target_position_pct",
    "authorized_notional",
    "authorized_qty",
    "estimated_fee",
    "risk_snapshot_json",
    "expires_at",
    "created_at",
)


def _protect_experiment_provenance(_mapper, _connection, target) -> None:
    state = sa_inspect(target)
    changed = [
        field for field in _EXPERIMENT_PROVENANCE_FIELDS
        if state.attrs[field].history.has_changes()
    ]
    if changed:
        raise ValueError(
            "backtest experiment provenance is immutable: " + ", ".join(changed))


def _protect_execution_intent_authorization(_mapper, _connection, target) -> None:
    state = sa_inspect(target)
    changed = [
        field for field in _EXECUTION_INTENT_AUTHORIZATION_FIELDS
        if state.attrs[field].history.has_changes()
    ]
    if changed:
        raise ValueError(
            "execution intent authorization is immutable: " + ", ".join(changed))


def _protect_immutable_update(_mapper, _connection, target) -> None:
    raise ValueError(f"{type(target).__name__} is append-only")


def _protect_audit_delete(_mapper, _connection, target) -> None:
    raise ValueError(f"{type(target).__name__} is permanent audit evidence")


event.listen(BacktestExperiment, "before_update", _protect_experiment_provenance)
event.listen(
    ExecutionIntent, "before_update", _protect_execution_intent_authorization)
for _audit_model in (
    AgentOutput,
    Decision,
    GateCheck,
    BacktestExperiment,
    BacktestExperimentResult,
    HoldoutSelection,
    HoldoutAccessEvent,
    RunMarketArtifact,
    ShadowBenchmarkSnapshot,
    ShadowBenchmarkMark,
    ExecutionIntent,
):
    event.listen(_audit_model, "before_delete", _protect_audit_delete)
for _immutable_model in (
    AgentOutput,
    Decision,
    GateCheck,
    BacktestExperimentResult,
    HoldoutSelection,
    HoldoutAccessEvent,
    RunMarketArtifact,
    ShadowBenchmarkSnapshot,
    ShadowBenchmarkMark,
):
    event.listen(_immutable_model, "before_update", _protect_immutable_update)


_SQLITE_AUDIT_TRIGGER_DDL = (
    """
    CREATE TRIGGER IF NOT EXISTS trg_agent_output_update
    BEFORE UPDATE ON agent_outputs
    BEGIN
        SELECT RAISE(ABORT, 'agent output is append-only');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_agent_output_delete
    BEFORE DELETE ON agent_outputs
    BEGIN
        SELECT RAISE(ABORT, 'agent output is permanent audit evidence');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_decision_update
    BEFORE UPDATE ON decisions
    BEGIN
        SELECT RAISE(ABORT, 'decision is append-only');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_decision_delete
    BEFORE DELETE ON decisions
    BEGIN
        SELECT RAISE(ABORT, 'decision is permanent audit evidence');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_gate_check_update
    BEFORE UPDATE ON gate_checks
    BEGIN
        SELECT RAISE(ABORT, 'gate check is append-only');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_gate_check_delete
    BEFORE DELETE ON gate_checks
    BEGIN
        SELECT RAISE(ABORT, 'gate check is permanent audit evidence');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_backtest_experiment_provenance_update
    BEFORE UPDATE ON backtest_experiments
    FOR EACH ROW WHEN
        OLD.hypothesis_id IS NOT NEW.hypothesis_id OR
        OLD.parent_experiment_id IS NOT NEW.parent_experiment_id OR
        OLD.campaign_key IS NOT NEW.campaign_key OR
        OLD.experiment_key IS NOT NEW.experiment_key OR
        OLD.spec_json IS NOT NEW.spec_json OR
        OLD.spec_fingerprint IS NOT NEW.spec_fingerprint OR
        OLD.code_fingerprint IS NOT NEW.code_fingerprint OR
        OLD.config_json IS NOT NEW.config_json OR
        OLD.config_fingerprint IS NOT NEW.config_fingerprint OR
        OLD.data_fingerprint IS NOT NEW.data_fingerprint OR
        OLD.data_artifact_json IS NOT NEW.data_artifact_json OR
        OLD.universe_json IS NOT NEW.universe_json OR
        OLD.universe_fingerprint IS NOT NEW.universe_fingerprint OR
        OLD.data_cutoff_at IS NOT NEW.data_cutoff_at OR
        OLD.data_start IS NOT NEW.data_start OR
        OLD.data_end IS NOT NEW.data_end OR
        OLD.row_count IS NOT NEW.row_count OR
        OLD.date_count IS NOT NEW.date_count OR
        OLD.validation_mode IS NOT NEW.validation_mode OR
        OLD.development_ratio IS NOT NEW.development_ratio OR
        OLD.created_at IS NOT NEW.created_at
    BEGIN
        SELECT RAISE(ABORT, 'backtest experiment provenance is immutable');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_backtest_experiment_delete
    BEFORE DELETE ON backtest_experiments
    BEGIN
        SELECT RAISE(ABORT, 'backtest experiment is permanent audit evidence');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_backtest_result_update
    BEFORE UPDATE ON backtest_experiment_results
    BEGIN
        SELECT RAISE(ABORT, 'backtest result is append-only');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_backtest_result_delete
    BEFORE DELETE ON backtest_experiment_results
    BEGIN
        SELECT RAISE(ABORT, 'backtest result is permanent audit evidence');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_holdout_selection_update
    BEFORE UPDATE ON holdout_selections
    BEGIN
        SELECT RAISE(ABORT, 'holdout selection is append-only');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_holdout_selection_delete
    BEFORE DELETE ON holdout_selections
    BEGIN
        SELECT RAISE(ABORT, 'holdout selection is permanent audit evidence');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_holdout_access_update
    BEFORE UPDATE ON holdout_access_events
    BEGIN
        SELECT RAISE(ABORT, 'holdout access is append-only');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_holdout_access_delete
    BEFORE DELETE ON holdout_access_events
    BEGIN
        SELECT RAISE(ABORT, 'holdout access is permanent audit evidence');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_execution_intent_authorization_update
    BEFORE UPDATE ON execution_intents
    FOR EACH ROW WHEN
        OLD.plan_id IS NOT NEW.plan_id OR
        OLD.idempotency_key IS NOT NEW.idempotency_key OR
        OLD.approved_by IS NOT NEW.approved_by OR
        OLD.approved_at IS NOT NEW.approved_at OR
        OLD.approval_quote_price IS NOT NEW.approval_quote_price OR
        OLD.approval_quote_asof IS NOT NEW.approval_quote_asof OR
        OLD.authorized_target_position_pct IS NOT NEW.authorized_target_position_pct OR
        OLD.authorized_notional IS NOT NEW.authorized_notional OR
        OLD.authorized_qty IS NOT NEW.authorized_qty OR
        OLD.estimated_fee IS NOT NEW.estimated_fee OR
        OLD.risk_snapshot_json IS NOT NEW.risk_snapshot_json OR
        OLD.expires_at IS NOT NEW.expires_at OR
        OLD.created_at IS NOT NEW.created_at
    BEGIN
        SELECT RAISE(ABORT, 'execution intent authorization is immutable');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_execution_intent_delete
    BEFORE DELETE ON execution_intents
    BEGIN
        SELECT RAISE(ABORT, 'execution intent is permanent audit evidence');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_run_market_artifact_update
    BEFORE UPDATE ON run_market_artifacts
    BEGIN
        SELECT RAISE(ABORT, 'run market artifact is append-only');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_run_market_artifact_delete
    BEFORE DELETE ON run_market_artifacts
    BEGIN
        SELECT RAISE(ABORT, 'run market artifact is permanent audit evidence');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_shadow_snapshot_update
    BEFORE UPDATE ON shadow_benchmark_snapshots
    BEGIN
        SELECT RAISE(ABORT, 'shadow snapshot is append-only');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_shadow_snapshot_delete
    BEFORE DELETE ON shadow_benchmark_snapshots
    BEGIN
        SELECT RAISE(ABORT, 'shadow snapshot is permanent audit evidence');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_shadow_mark_update
    BEFORE UPDATE ON shadow_benchmark_marks
    BEGIN
        SELECT RAISE(ABORT, 'shadow mark is append-only');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_shadow_mark_delete
    BEFORE DELETE ON shadow_benchmark_marks
    BEGIN
        SELECT RAISE(ABORT, 'shadow mark is permanent audit evidence');
    END
    """,
)


def install_sqlite_audit_triggers(connection) -> None:
    """Install database-level immutability for new and already-existing tables."""
    if connection.dialect.name != "sqlite":
        return
    existing = {
        row[0]
        for row in connection.execute(text(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ))
    }
    required = {
        "agent_outputs",
        "decisions",
        "gate_checks",
        "backtest_experiments",
        "backtest_experiment_results",
        "holdout_selections",
        "holdout_access_events",
        "execution_intents",
        "run_market_artifacts",
        "shadow_benchmark_snapshots",
        "shadow_benchmark_marks",
    }
    if not required.issubset(existing):
        return
    for statement in _SQLITE_AUDIT_TRIGGER_DDL:
        connection.exec_driver_sql(statement)


def _install_audit_triggers_after_create(_target, connection, **_kwargs) -> None:
    install_sqlite_audit_triggers(connection)


event.listen(Base.metadata, "after_create", _install_audit_triggers_after_create)

