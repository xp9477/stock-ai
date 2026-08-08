from datetime import datetime

from sqlalchemy import Boolean, Float, ForeignKey, Integer, String, Text, DateTime
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def now() -> datetime:
    return datetime.now()


class Model(Base):
    """参赛模型或合议组合,各自持有独立虚拟账户。"""

    __tablename__ = "models"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(50), unique=True)
    model_id: Mapped[str] = mapped_column(String(100), default="")  # API model 名; ensemble 为空
    type: Mapped[str] = mapped_column(String(10), default="llm")  # llm / ensemble
    members: Mapped[str] = mapped_column(Text, default="[]")  # ensemble 成员 model 主键 JSON 列表
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
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

