from unittest.mock import patch

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from app.api.routes import (ModelCreate, ModelUpdate, create_model, delete_model,
                            get_portfolio, rules_rebalance, rules_rebalance_one,
                            status, strategies_board, update_model)
from app.models import Account, Model, Order
from app.seed import DEFAULT_MODELS, ensure_rule_strategies, replacement_for, seed_models


def test_seed_does_not_create_or_fund_rule_accounts(db):
    historical = Model(
        name="历史 S2", model_id="s2_weekly", type="rule", enabled=True,
    )
    db.add(historical)
    db.commit()

    with patch("app.runtime_settings.get_setting", return_value=100_000.0):
        assert ensure_rule_strategies(db) == []
        seed_models(db)

    assert db.query(Model).filter(Model.type == "rule").count() == 1
    assert db.query(Account).filter(Account.model_pk == historical.id).count() == 0
    official = db.query(Model).filter(Model.type == "ensemble").one()
    assert official.is_official_strategy is True
    assert db.query(Account).filter(Account.model_pk == official.id).count() == 1
    assert db.query(Account).count() == 1


def test_seed_replaces_legacy_opus_without_changing_primary_key(db):
    legacy = Model(
        name="Opus 4.6 Thinking", model_id="claude-opus-4-6-thinking",
        type="llm", enabled=True,
    )
    db.add(legacy)
    db.commit()
    original_id = legacy.id

    seed_models(db)

    db.refresh(legacy)
    assert legacy.id == original_id
    assert legacy.name == "Grok 4.6"
    assert legacy.model_id == "grok-4.6"


def test_default_models_replace_grok_45_with_gpt_sol_high():
    ids = [item["model_id"] for item in DEFAULT_MODELS]
    names = [item["name"] for item in DEFAULT_MODELS]
    assert "grok-4.5" not in ids
    assert "Grok 4.5" not in names
    assert ids == [
        "grok-4.6",
        "gpt-5.6-sol",
        "gemini-3.7-flash-high",
    ]


def test_seed_replaces_legacy_grok_45_without_changing_primary_key(db):
    legacy = Model(
        name="Grok 4.5", model_id="grok-4.5",
        type="llm", enabled=True,
    )
    db.add(legacy)
    db.commit()
    original_id = legacy.id

    seed_models(db)

    db.refresh(legacy)
    assert legacy.id == original_id
    assert legacy.name == "Grok 4.6"
    assert legacy.model_id == "grok-4.6"


def test_seed_replaces_grok_45_with_gpt_when_grok_46_already_exists(db):
    retired = Model(name="Grok 4.5", model_id="grok-4.5", type="llm", enabled=True)
    current = Model(name="Grok 4.6", model_id="grok-4.6", type="llm", enabled=True)
    db.add_all([retired, current])
    db.commit()
    original_id = retired.id

    seed_models(db)

    db.refresh(retired)
    db.refresh(current)
    assert retired.id == original_id
    assert retired.name == "GPT 5.6 Sol High"
    assert retired.model_id == "gpt-5.6-sol"
    assert current.model_id == "grok-4.6"
    assert replacement_for(retired, {"gpt-5.6-sol", "grok-4.6"}) is None


def test_seed_puts_grok_46_first_among_default_advisors(db):
    first = Model(name="GPT 5.6 Sol High", model_id="gpt-5.6-sol", type="llm")
    second = Model(name="Gemini 3.7 Flash", model_id="gemini-3.7-flash-high", type="llm")
    third = Model(name="Grok 4.6", model_id="grok-4.6", type="llm")
    db.add_all([first, second, third])
    db.commit()

    seed_models(db)

    db.refresh(first)
    db.refresh(second)
    db.refresh(third)
    assert first.model_id == "grok-4.6"
    assert second.model_id == "gpt-5.6-sol"
    assert third.model_id == "gemini-3.7-flash-high"


def test_cannot_create_a_second_capitalized_ensemble_account(db):
    advisors = []
    for index in range(2):
        model = Model(name=f"顾问 {index}", model_id=f"llm-{index}", type="llm")
        db.add(model)
        db.flush()
        advisors.append(model.id)
    existing = Model(name="官方策略", type="ensemble", members=str(advisors))
    db.add(existing)
    db.commit()

    with pytest.raises(HTTPException) as caught:
        create_model(ModelCreate(name="第二资金账户", type="ensemble", members=advisors), db)

    assert caught.value.status_code == 409
    assert db.query(Model).filter(Model.type == "ensemble").count() == 1
    assert db.query(Account).count() == 0


def test_database_allows_at_most_one_official_strategy(db):
    db.add_all([
        Model(name="官方一", type="ensemble", is_official_strategy=True),
        Model(name="官方二", type="ensemble", is_official_strategy=True),
    ])
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_seed_fails_closed_when_legacy_ensembles_are_ambiguous(db):
    db.add_all([
        Model(name="旧合议一", type="ensemble", enabled=True),
        Model(name="旧合议二", type="ensemble", enabled=True),
    ])
    db.commit()

    seed_models(db)

    assert db.query(Model).filter(
        Model.is_official_strategy.is_(True)).count() == 0
    assert db.query(Account).count() == 0


def test_seed_migrates_only_unambiguous_enabled_ensemble(db):
    selected = Model(name="仍启用的旧合议", type="ensemble", enabled=True)
    historical = Model(name="停用的旧合议", type="ensemble", enabled=False)
    db.add_all([selected, historical])
    db.commit()

    seed_models(db)

    db.refresh(selected)
    db.refresh(historical)
    assert selected.is_official_strategy is True
    assert historical.is_official_strategy is False
    assert db.query(Account).filter(Account.model_pk == selected.id).count() == 1
    assert db.query(Account).filter(Account.model_pk == historical.id).count() == 0


def test_seed_does_not_promote_or_fund_a_single_disabled_ensemble(db):
    historical = Model(name="停用的唯一旧合议", type="ensemble", enabled=False)
    db.add(historical)
    db.commit()

    seed_models(db)

    db.refresh(historical)
    assert historical.is_official_strategy is False
    assert db.query(Account).filter(Account.model_pk == historical.id).count() == 0


def test_disabling_one_ambiguous_legacy_ensemble_promotes_the_remaining_one(db):
    selected = Model(name="remaining legacy ensemble", type="ensemble", enabled=True)
    historical = Model(name="retired legacy ensemble", type="ensemble", enabled=True)
    db.add_all([selected, historical])
    db.commit()

    assert update_model(historical.id, ModelUpdate(enabled=False), db) == {"ok": True}

    db.refresh(selected)
    db.refresh(historical)
    assert selected.enabled is True
    assert selected.is_official_strategy is True
    assert historical.enabled is False
    assert historical.is_official_strategy is False


@pytest.mark.parametrize(
    ("call", "args"),
    [
        (rules_rebalance, ()),
        (rules_rebalance_one, ("s2_weekly",)),
    ],
)
def test_rule_rebalance_api_is_gone_without_writes(db, call, args):
    before = (db.query(Model).count(), db.query(Account).count(), db.query(Order).count())

    with pytest.raises(HTTPException) as caught:
        call(*args, db=db)

    assert caught.value.status_code == 410
    assert caught.value.detail["code"] == "capitalized_rule_racing_retired"
    assert (db.query(Model).count(), db.query(Account).count(), db.query(Order).count()) == before


@pytest.mark.parametrize(
    ("call", "args"),
    [
        (update_model, (ModelUpdate(name="不得改名", enabled=False),)),
        (delete_model, ()),
        (get_portfolio, ()),
    ],
)
def test_historical_rule_model_is_read_only_and_never_lazy_funded(db, call, args):
    historical = Model(
        name="历史只读规则", model_id="s2_weekly", type="rule", enabled=True,
    )
    db.add(historical)
    db.commit()
    before = {
        "name": historical.name,
        "enabled": historical.enabled,
        "models": db.query(Model).count(),
        "accounts": db.query(Account).count(),
        "orders": db.query(Order).count(),
    }

    with pytest.raises(HTTPException) as caught:
        call(historical.id, *args, db=db)

    assert caught.value.status_code == 410
    assert caught.value.detail["code"] == "capitalized_rule_racing_retired"
    db.refresh(historical)
    assert historical.name == before["name"]
    assert historical.enabled is before["enabled"]
    assert db.query(Model).count() == before["models"]
    assert db.query(Account).count() == before["accounts"] == 0
    assert db.query(Order).count() == before["orders"] == 0


def test_nonofficial_legacy_ensemble_cannot_lazy_create_account(db):
    historical = Model(
        name="历史合议", type="ensemble", enabled=True,
        is_official_strategy=False,
    )
    db.add(historical)
    db.commit()

    with pytest.raises(HTTPException) as caught:
        get_portfolio(historical.id, db=db)

    assert caught.value.status_code == 410
    assert caught.value.detail["code"] == "historical_ensemble_not_capitalized"
    assert db.query(Account).count() == 0


def test_strategy_board_get_is_read_only_and_does_not_seed_rules(db):
    values = {
        "race.min_trade_days": 60,
        "race.min_closed_trades": 100,
        "factor.top_n": 10,
    }
    with patch("app.strategies.board.get_setting", side_effect=values.__getitem__):
        result = strategies_board(db)

    assert result["capital_execution_enabled"] is False
    assert result["historical_only"] is True
    assert db.query(Model).filter(Model.type == "rule").count() == 0
    assert db.query(Account).count() == 0


def test_status_declares_manual_ticket_readiness_and_retired_rule_compatibility(db):
    db.add(Model(
        name="官方状态策略", type="ensemble", enabled=True,
        is_official_strategy=True,
    ))
    db.commit()
    values = {
        "selector.pool_max": 30,
        "factor.top_n": 10,
        "capital.authorized_capital": 100_000.0,
        "capital.max_stock_exposure": 80_000.0,
        "secrets.fuyao_api_key": "",
        "secrets.llm_api_key": "",
        "notifications.bark.enabled": False,
        "notifications.bark.device_key": "",
        "debug.show_agent_io_default": False,
        "execution.require_manual_confirmation": False,
        "execution.auto_fill_tickets": True,
    }
    with patch("app.runtime_settings.get_setting", side_effect=values.__getitem__), \
            patch("app.scheduler.is_enabled", return_value=False), \
            patch("app.scheduler.schedule_times", return_value="决策 16:00"), \
            patch("app.scheduler.next_run_time", return_value=None):
        result = status(db)

    assert result["execution_mode"] == "auto_fill"
    assert result["official_disclosure_provider"] is False
    assert result["live_funds_ready"] is False
    assert set(result["live_funds_blockers"]) == {
        "official_disclosure_provider_not_configured",
        "broker_execution_not_configured",
        "authentication_not_configured",
        "shadow_benchmark_not_automated",
        "minimum_forward_validation_not_proven",
    }
    assert result["authorized_capital"] == 100_000.0
    assert result["max_stock_exposure"] == 80_000.0
    assert result["destructive_reset_enabled"] is False
    assert result["capitalized_rule_racing_enabled"] is False
    assert result["bark_enabled"] is False
    assert result["bark_configured"] is False
    assert result["rule_enabled_count"] == 0
    assert result["official_strategy_count"] == 1
