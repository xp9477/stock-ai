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
    trigger: Mapped[str] = mapped_column(String(10))  # manual / schedule
    status: Mapped[str] = mapped_column(String(10), default="running")  # running / done / failed
    error: Mapped[str] = mapped_column(Text, default="")
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
