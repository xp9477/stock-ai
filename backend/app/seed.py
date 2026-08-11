"""种子数据：LLM 顾问与唯一的官方合议策略账户。"""
import json
import logging

from sqlalchemy.orm import Session

from .config import settings
from .models import Account, Model

DEFAULT_MODELS = [
    {"name": "Grok 4.5", "model_id": "grok-4.5"},
    {"name": "Gemini 3.6 Flash", "model_id": "gemini-3.6-flash-high"},
    {"name": "Opus 4.6 Thinking", "model_id": "claude-opus-4-6-thinking"},
]

# 仅用于识别旧库中的历史规则账户。新安装不再创建资本化规则账户。
RULE_STRATEGIES = [
    {"name": "S2周频前10", "model_id": "s2_weekly", "type": "rule"},
    {"name": "池内等权", "model_id": "pool_equal", "type": "rule"},
]

logger = logging.getLogger(__name__)


def ensure_account(db: Session, model_pk: int):
    if not db.query(Account).filter(Account.model_pk == model_pk).first():
        from .runtime_settings import get_setting
        cash = float(get_setting("account.initial_cash"))
        db.add(Account(model_pk=model_pk, cash=cash, initial_cash=cash))
        db.flush()


def ensure_rule_strategies(db: Session):
    """兼容旧调用方的只读 no-op；绝不创建规则模型或资金账户。

    历史规则账户及其订单/持仓仍留在数据库中作为证据，但资本化规则赛马已退役。
    """
    return []


def seed_models(db: Session):
    llm_models = db.query(Model).filter(Model.type == "llm").order_by(Model.id).all()
    if not llm_models:
        llm_pks = []
        for item in DEFAULT_MODELS:
            model = Model(name=item["name"], model_id=item["model_id"], type="llm")
            db.add(model)
            db.flush()
            llm_pks.append(model.id)
        llm_models = db.query(Model).filter(Model.id.in_(llm_pks)).all()

    ensembles = db.query(Model).filter(Model.type == "ensemble").order_by(Model.id).all()
    official = next((model for model in ensembles if model.is_official_strategy), None)
    if official is None and not ensembles and len(llm_models) >= 2:
        official = Model(
            name="三模合议",
            type="ensemble",
            members=json.dumps([model.id for model in llm_models]),
            is_official_strategy=True,
        )
        db.add(official)
        db.flush()
    elif official is None:
        enabled = [model for model in ensembles if model.enabled]
        # Existing databases commonly have one pre-contract ensemble.  A
        # single unambiguous candidate can be migrated safely; multiple active
        # candidates are left without capital authority instead of guessing.
        if len(enabled) == 1:
            official = enabled[0]
            official.is_official_strategy = True
        elif ensembles:
            logger.error(
                "检测到多个 ensemble 且无法唯一确定官方策略；全部保持无资金授权"
            )

    # Only the unique official strategy may receive an account.  Historical
    # ensemble/rule accounts are preserved but never topped up or recreated.
    if official is not None and official.enabled:
        ensure_account(db, official.id)
    db.commit()
