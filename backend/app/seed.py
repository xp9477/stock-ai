"""种子数据：LLM 顾问与唯一的官方合议策略账户。"""
import json
import logging

from sqlalchemy.orm import Session

from .config import settings
from .models import Account, Model

DEFAULT_MODELS = [
    {"name": "Grok 4.6", "model_id": "grok-4.6"},
    {"name": "GPT 5.6 Sol High", "model_id": "gpt-5.6-sol"},
    {"name": "Gemini 3.8 Flash High", "model_id": "gemini-3.8-flash-high"},
]

LEGACY_MODEL_REPLACEMENTS = {
    "claude-opus-4-6-thinking": ("Grok 4.6", "grok-4.6"),
    "gpt-5.6-sol-high": ("GPT 5.6 Sol High", "gpt-5.6-sol"),
    "gemini-3.6-flash-high": ("Gemini 3.8 Flash High", "gemini-3.8-flash-high"),
    "gemini-3.7-flash-high": ("Gemini 3.8 Flash High", "gemini-3.8-flash-high"),
}

# Grok 4.5 已从网关下线。已有 4.6 时，这个顾问位改成 GPT Sol High；
# 否则原地升级成 4.6，避免选股还打到已删除的 model id。
_RETIRED_GROK_45_IDS = frozenset({"grok-4.5", "grok-4-5"})


def normalize_model_id(model_id: str) -> str:
    return str(model_id or "").strip().lower().replace("_", "-")


def is_retired_grok_45(model_id: str) -> bool:
    return normalize_model_id(model_id) in _RETIRED_GROK_45_IDS


def _align_default_advisor_order(db: Session, llm_models: list[Model]) -> None:
    """选股取 id 最小的启用 LLM，所以默认三人组按 DEFAULT_MODELS 顺序排到最前几个主键。"""
    wanted = [(item["name"], item["model_id"]) for item in DEFAULT_MODELS]
    wanted_ids = {normalize_model_id(model_id) for _, model_id in wanted}
    holders = [
        model for model in llm_models
        if normalize_model_id(model.model_id) in wanted_ids
    ]
    if len(holders) != len(wanted):
        return
    holders.sort(key=lambda model: model.id)
    current = [normalize_model_id(model.model_id) for model in holders]
    target = [normalize_model_id(model_id) for _, model_id in wanted]
    if current == target:
        return
    # name / model_id 都有唯一约束，必须先腾出旧值再写入目标顺序。
    for index, model in enumerate(holders):
        model.name = f"__reorder_{index}__"
        model.model_id = f"__reorder_{index}__"
    db.flush()
    for model, (name, model_id) in zip(holders, wanted):
        model.name, model.model_id = name, model_id
    logger.warning(
        "已重排默认顾问顺序: %s",
        " → ".join(model_id for _, model_id in wanted),
    )


def replacement_for(model: Model, sibling_ids: set[str] | None = None) -> tuple[str, str] | None:
    mid = normalize_model_id(model.model_id)
    explicit = LEGACY_MODEL_REPLACEMENTS.get(mid)
    if explicit:
        name, target_id = explicit
        if sibling_ids:
            other_ids = {normalize_model_id(s) for s in sibling_ids if s != model.model_id}
            if normalize_model_id(target_id) in other_ids:
                return None
        return explicit
    if is_retired_grok_45(mid):
        siblings = {normalize_model_id(item) for item in (sibling_ids or set())}
        siblings.discard(mid)
        if "grok-4.6" in siblings:
            return ("GPT 5.6 Sol High", "gpt-5.6-sol")
        return ("Grok 4.6", "grok-4.6")
    return None

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

    # 保留数据库主键，使既有 ensemble.members 和历史审计关联不变；只替换
    # 已确认不可用的旧模型名称与网关 ID。
    sibling_ids = {item.model_id for item in llm_models}
    for model in llm_models:
        replacement = replacement_for(model, sibling_ids)
        if replacement:
            old_id = model.model_id
            sibling_ids.discard(old_id)
            model.name, model.model_id = replacement
            sibling_ids.add(model.model_id)
            logger.warning("已替换停用模型 %s → %s (%s)", old_id, model.model_id, model.name)

    _align_default_advisor_order(db, llm_models)

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
