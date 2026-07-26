"""种子数据:初始参赛模型与默认合议组合。"""
import json

from sqlalchemy.orm import Session

from .config import settings
from .models import Account, Model

DEFAULT_MODELS = [
    {"name": "Grok 4.5", "model_id": "grok-4.5"},
    {"name": "Opus 5", "model_id": "claude-opus-5"},
    {"name": "Fable 5", "model_id": "claude-fable-5"},
]


def ensure_account(db: Session, model_pk: int):
    if not db.query(Account).filter(Account.model_pk == model_pk).first():
        db.add(Account(model_pk=model_pk, cash=settings.initial_cash,
                       initial_cash=settings.initial_cash))


def seed_models(db: Session):
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
    for model in db.query(Model).all():
        ensure_account(db, model.id)
    db.commit()
