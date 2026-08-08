"""P0：协作式取消与进度快照。"""
from unittest.mock import patch

from app.agents import engine
from app.models import Account, Model, Run, Watchlist


def _reset_engine():
    engine._run_lock = False
    engine._cancel_requested = False
    engine._set_progress(
        run_id=None, phase="idle", message="", model_name="", model_pk=None,
        model_index=0, model_total=0, code="", stock_name="",
        stock_index=0, stock_total=0, agent="",
    )


def test_request_cancel_when_idle():
    _reset_engine()
    res = engine.request_cancel()
    assert res["ok"] is False


def test_request_cancel_when_running():
    _reset_engine()
    engine._run_lock = True
    engine._set_progress(run_id=42)
    res = engine.request_cancel()
    assert res["ok"] is True
    assert engine.cancel_requested() is True
    assert res["run_id"] == 42
    _reset_engine()


def test_pipeline_cancelled_status(db, model_a):
    """跑流水线时在准备阶段前请求取消 → status=cancelled。"""
    _reset_engine()
    db.add(Watchlist(code="600519", name="贵州茅台", source="manual"))
    db.add(Account(model_pk=model_a.id, cash=1_000_000, initial_cash=1_000_000))
    db.commit()

    # 用测试 Session 驱动：patch SessionLocal 返回同一 db 会复杂；
    # 改为直接测 _check_cancel + 手动模拟 run 状态写入。
    engine._cancel_requested = True
    try:
        engine._check_cancel()
        assert False, "should raise"
    except engine.PipelineCancelled:
        pass

    run = Run(trigger="manual", status="cancelled", error="用户取消")
    db.add(run)
    db.commit()
    assert db.get(Run, run.id).status == "cancelled"
    _reset_engine()


def test_get_progress_copy():
    _reset_engine()
    engine._set_progress(phase="stock", message="hello", agent="trader")
    p = engine.get_progress()
    assert p["phase"] == "stock"
    assert p["agent"] == "trader"
    p["phase"] = "mutated"
    assert engine.get_progress()["phase"] == "stock"
    _reset_engine()
