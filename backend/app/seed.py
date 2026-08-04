"""种子数据:初始参赛模型、合议组合与规则组账户。"""
import json

from sqlalchemy.orm import Session

from .config import settings
from .models import Account, Model

DEFAULT_MODELS = [
    {"name": "Grok 4.5", "model_id": "grok-4.5"},
    {"name": "Gemini 3.6 Flash", "model_id": "gemini-3.6-flash-high"},
    {"name": "Opus 4.6 Thinking", "model_id": "claude-opus-4-6-thinking"},
]

# 规则组（零 LLM，与 AI 同场赛马）
RULE_STRATEGIES = [
    {"name": "S2周频前10", "model_id": "s2_weekly", "type": "rule"},
    {"name": "池内等权", "model_id": "pool_equal", "type": "rule"},
]


def ensure_account(db: Session, model_pk: int):
    if not db.query(Account).filter(Account.model_pk == model_pk).first():
        db.add(Account(model_pk=model_pk, cash=settings.initial_cash,
                       initial_cash=settings.initial_cash))
        db.flush()


def ensure_rule_strategies(db: Session):
    """幂等：保证规则账户存在（老库升级也能补上）。"""
    for item in RULE_STRATEGIES:
        existing = (
            db.query(Model)
            .filter(Model.type == "rule", Model.model_id == item["model_id"])
            .first()
        )
        if existing:
            continue
        # 名称冲突时加后缀
        name = item["name"]
        if db.query(Model).filter(Model.name == name).first():
            name = f"{name}-{item['model_id']}"
        model = Model(name=name, model_id=item["model_id"], type="rule", members="[]")
        db.add(model)
        db.flush()
        ensure_account(db, model.id)


def seed_models(db: Session):
    if db.query(Model).filter(Model.type == "llm").count() == 0 and \
            db.query(Model).filter(Model.type == "ensemble").count() == 0:
        # 仅当完全没有 LLM/合议时种默认三模型（避免覆盖用户已有库的空规则态）
        if db.query(Model).count() == 0:
            llm_pks = []
            for item in DEFAULT_MODELS:
                model = Model(name=item["name"], model_id=item["model_id"], type="llm")
                db.add(model)
                db.flush()
                llm_pks.append(model.id)
            ensemble = Model(name="三模合议", type="ensemble", members=json.dumps(llm_pks))
            db.add(ensemble)
            db.flush()
    ensure_rule_strategies(db)
    for model in db.query(Model).all():
        ensure_account(db, model.id)
    db.commit()
