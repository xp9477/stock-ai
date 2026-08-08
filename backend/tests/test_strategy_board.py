"""P2 策略对照台：锚角色、夏普排序、样本授冠。"""
from datetime import datetime, timedelta

from app.models import Account, EquitySnapshot, Model
from app.strategies.board import build_strategy_board
from app.strategies.rule_runner import POOL_EQUAL, S2_WEEKLY


def _seed_rule_arms(db):
    arms = []
    for name, mid in (("池内等权", POOL_EQUAL), ("S2周频前10", S2_WEEKLY)):
        m = Model(name=name, model_id=mid, type="rule", enabled=True)
        db.add(m)
        db.flush()
        db.add(Account(model_pk=m.id, cash=1_000_000, initial_cash=1_000_000))
        arms.append(m)
    db.commit()
    return arms


def _add_equity_path(db, model_pk, start, daily_rets):
    """写入日频权益快照。start: datetime, daily_rets: list of daily returns."""
    eq = 1_000_000.0
    t = start
    for r in daily_rets:
        eq *= 1 + r
        db.add(EquitySnapshot(
            model_pk=model_pk, total_equity=eq, cash=eq * 0.2, market_value=eq * 0.8,
            created_at=t,
        ))
        t += timedelta(days=1)
    db.commit()


def test_board_anchor_and_sort(db, monkeypatch):
    anchor, s2 = _seed_rule_arms(db)
    start = datetime(2025, 1, 1)
    # 锚：平稳小涨
    _add_equity_path(db, anchor.id, start, [0.001] * 5)
    # S2：更高波动但更高夏普路径
    _add_equity_path(db, s2.id, start, [0.003] * 5)

    monkeypatch.setattr(
        "app.strategies.board.get_setting",
        lambda k, db=None: {
            "race.min_trade_days": 3,
            "race.min_closed_trades": 0,
            "factor.top_n": 10,
        }.get(k, 0),
    )
    monkeypatch.setattr("app.strategies.board.closed_trade_count", lambda *a, **k: 0)
    # mark_sample_ok needs closed_trades from metrics - we pass 0 and min_trades 0
    board = build_strategy_board(db)
    assert board["anchor_model_id"] == POOL_EQUAL
    arms = {a["model_id"]: a for a in board["arms"]}
    assert arms[POOL_EQUAL]["role"] == "anchor"
    assert arms[S2_WEEKLY]["role"] == "competitive"
    assert arms[POOL_EQUAL]["source"] == "builtin"
    # 锚在列表前部
    assert board["arms"][0]["model_id"] == POOL_EQUAL
    # 有快照则夏普可算
    assert arms[S2_WEEKLY]["exists"] is True
    assert isinstance(arms[S2_WEEKLY]["sharpe"], (int, float))


def test_board_crown_requires_sample_ok(db, monkeypatch):
    anchor, s2 = _seed_rule_arms(db)
    start = datetime(2025, 1, 1)
    _add_equity_path(db, s2.id, start, [0.01] * 10)
    _add_equity_path(db, anchor.id, start, [0.001] * 10)

    # 门槛极高 → 无人 sample_ok → 无冠
    monkeypatch.setattr(
        "app.strategies.board.get_setting",
        lambda k, db=None: {
            "race.min_trade_days": 999,
            "race.min_closed_trades": 999,
            "factor.top_n": 10,
        }.get(k, 0),
    )
    monkeypatch.setattr("app.strategies.board.closed_trade_count", lambda *a, **k: 0)
    board = build_strategy_board(db)
    assert board["champion_model_id"] is None
    assert all(not a.get("crown") for a in board["arms"])
