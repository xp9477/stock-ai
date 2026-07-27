"""自动选股测试: 规则初筛、JSON 解析、股池生命周期、完整 job。"""
import json
from unittest.mock import patch

import pandas as pd

from app.agents import selector
from app.data import market
from app.models import AgentOutput, Position, Run, Watchlist


# ---------- 规则初筛 ----------

def make_snapshot(rows):
    return pd.DataFrame(rows, columns=["代码", "名称", "最新价", "涨跌幅", "成交额"])


def test_screen_filters():
    snap = make_snapshot([
        ("sh600000", "浦发银行", 10.0, 1.0, 5e8),      # 合格
        ("bj920000", "北交所股", 10.0, 1.0, 5e8),      # 北交所排除
        ("sz000001", "*ST平安", 10.0, 1.0, 5e8),       # ST 排除
        ("sz000002", "万科退", 10.0, 1.0, 5e8),        # 退市排除
        ("sh600001", "低价股", 2.9, 1.0, 5e8),         # 价格过低
        ("sh600002", "高价股", 101.0, 1.0, 5e8),       # 价格过高
        ("sh600003", "缩量股", 10.0, 1.0, 1.9e8),      # 成交额不足
        ("sh600004", "跌停股", 10.0, -3.1, 5e8),       # 跌幅超限
        ("sh600005", "涨停股", 10.0, 7.1, 5e8),        # 涨幅超限
        ("sh605999", "非白名单前缀", 10.0, 1.0, 5e8),   # 60 开头,合格
        ("sh688001", "科创板", 50.0, 3.0, 6e8),        # 合格
    ])
    out = market.screen_candidates(snapshot=snap)
    codes = {c["code"] for c in out}
    assert codes == {"600000", "605999", "688001"}
    # 按成交额降序
    assert out[0]["code"] == "688001"


def test_screen_excludes_pool_and_caps_top_n():
    rows = [(f"sh60{i:04d}", f"股{i}", 10.0, 1.0, (100 - i) * 1e8) for i in range(40)]
    snap = make_snapshot(rows)
    out = market.screen_candidates(exclude_codes={"600000"}, snapshot=snap)
    assert len(out) == market.SCREEN_TOP_N
    assert all(c["code"] != "600000" for c in out)


# ---------- JSON 解析 ----------

def test_parse_selector_json_ok():
    text = ('选股思路: 略。\n'
            '{"picks": [{"code": "600519", "reason": "白酒龙头"}], "keep": ["002415"]}')
    parsed = selector.parse_selector_json(text)
    assert parsed["picks"] == [{"code": "600519", "reason": "白酒龙头"}]
    assert parsed["keep"] == ["002415"]


def test_parse_selector_json_nested_and_last_wins():
    text = ('{"picks": [], "keep": []}\n中间文字\n'
            '{"picks": [{"code": "300750", "reason": "宁德"}], "keep": []}')
    parsed = selector.parse_selector_json(text)
    assert parsed["picks"][0]["code"] == "300750"


def test_parse_selector_json_invalid():
    assert selector.parse_selector_json("完全没有 JSON") is None
    assert selector.parse_selector_json('{"action": "buy"}') is None


def test_parse_selector_json_drops_bad_codes():
    text = '{"picks": [{"code": "abc", "reason": "x"}, {"code": "600001", "reason": "y"}], "keep": ["12345", "000002"]}'
    parsed = selector.parse_selector_json(text)
    assert [p["code"] for p in parsed["picks"]] == ["600001"]
    assert parsed["keep"] == ["000002"]


# ---------- 生命周期 ----------

def test_lifecycle_miss_count_and_removal(db, model_a):
    w1 = Watchlist(code="600001", name="甲", source="auto", miss_count=2)
    w2 = Watchlist(code="600002", name="乙", source="auto", miss_count=0)
    db.add_all([w1, w2])
    db.commit()
    removed = selector._apply_lifecycle(db, [w1, w2], favored={"600002"})
    assert removed == ["600001"]  # miss 达 3 且无持仓
    assert db.query(Watchlist).filter(Watchlist.code == "600001").first() is None
    assert w2.miss_count == 0


def test_lifecycle_held_stock_not_removed(db, model_a):
    w = Watchlist(code="600003", name="丙", source="auto", miss_count=2)
    db.add(w)
    db.add(Position(model_pk=model_a.id, code="600003", name="丙",
                    total_qty=100, available_qty=100, avg_cost=10.0))
    db.commit()
    removed = selector._apply_lifecycle(db, [w], favored=set())
    assert removed == []
    assert w.miss_count == 3  # 只累计不移除


def test_lifecycle_manual_untouched(db):
    manual = Watchlist(code="600519", name="茅台", source="manual", miss_count=0)
    db.add(manual)
    db.commit()
    # manual 不进入 auto_items,不受影响
    removed = selector._apply_lifecycle(db, [], favored=set())
    assert removed == []
    assert db.query(Watchlist).filter(Watchlist.code == "600519").first() is not None


# ---------- 完整 job(mock LLM 与数据) ----------

FAKE_CANDIDATES = [
    {"code": "600001", "name": "股一", "price": 10.0, "pct_change": 1.0, "turnover": 5e8},
    {"code": "600002", "name": "股二", "price": 20.0, "pct_change": 2.0, "turnover": 4e8},
]


def run_selector_with(db, chat_return, pool_max=8):
    session_factory = lambda: db  # noqa: E731
    with patch("app.database.SessionLocal", session_factory), \
         patch("app.agents.selector.market.screen_candidates",
               return_value=FAKE_CANDIDATES), \
         patch("app.agents.selector.market.market_overview_text",
               return_value="大盘中性"), \
         patch("app.agents.selector.llm.chat", side_effect=chat_return), \
         patch.object(selector.settings, "pool_max", pool_max):
        real_close = db.close
        db.close = lambda: None  # 防止 job 关闭共享测试 session
        try:
            return selector.run_selector()
        finally:
            db.close = real_close


def test_run_selector_adds_picks(db, model_a):
    reply = json.dumps({"picks": [{"code": "600001", "reason": "看好"}], "keep": []})
    run_id = run_selector_with(db, lambda *a, **k: f"思路...\n{reply}")
    run = db.get(Run, run_id)
    assert run.status == "done" and run.trigger == "selector"
    item = db.query(Watchlist).filter(Watchlist.code == "600001").first()
    assert item is not None and item.source == "auto" and item.select_reason == "看好"
    out = db.query(AgentOutput).filter(AgentOutput.run_id == run_id).first()
    assert out.agent == "selector" and out.code == "SELECT"


def test_run_selector_respects_pool_max(db, model_a):
    db.add(Watchlist(code="000001", name="占位", source="manual"))
    db.commit()
    reply = json.dumps({"picks": [{"code": "600001", "reason": "a"},
                                  {"code": "600002", "reason": "b"}], "keep": []})
    run_selector_with(db, lambda *a, **k: reply, pool_max=2)
    codes = {w.code for w in db.query(Watchlist).all()}
    assert codes == {"000001", "600001"}  # 只剩 1 空位,600002 被截断


def test_run_selector_bad_json_skips(db, model_a):
    calls = []

    def bad_chat(*a, **k):
        calls.append(1)
        return "没有 JSON"

    run_id = run_selector_with(db, bad_chat)
    run = db.get(Run, run_id)
    assert run.status == "failed" and "解析失败" in run.error
    assert len(calls) == 2  # 重试了一次
    assert db.query(Watchlist).count() == 0


def test_run_selector_ignores_hallucinated_code(db, model_a):
    reply = json.dumps({"picks": [{"code": "999999", "reason": "臆造"}], "keep": []})
    run_selector_with(db, lambda *a, **k: reply)
    assert db.query(Watchlist).count() == 0
