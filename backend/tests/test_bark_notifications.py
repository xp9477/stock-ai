"""Bark is best-effort and never part of the trading state transition."""
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.notifications import bark


def _settings(**updates):
    values = {
        "enabled": True,
        "server_url": "https://api.day.app",
        "device_key": "secret-device-key",
        "open_url": "http://192.168.1.20:18000",
        "group": "stock-ai",
        "timeout_sec": 8,
        "notify_candidates": True,
        "notify_risk_reviews": True,
        "notify_failures": True,
    }
    values.update(updates)
    return values


def _get_setting(values):
    def getter(key):
        if key.startswith("notifications.bark."):
            return values[key.removeprefix("notifications.bark.")]
        if key in (
            "execution.require_manual_confirmation",
            "execution.auto_fill_tickets",
        ):
            return values.get(key, key.endswith("auto_fill_tickets"))
        return values[key]
    return getter


def _response():
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"code": 200, "message": "success"}
    return response


def test_bark_posts_device_key_in_json_not_url_and_adds_click_target():
    values = _settings()
    with patch("app.notifications.bark.get_setting", side_effect=_get_setting(values)), \
         patch("app.notifications.bark.requests.post", return_value=_response()) as post:
        result = bark.send(
            "待处理", "请确认", path="/orders", dedupe_key="test:json-body")

    assert result["ok"] is True
    url = post.call_args.args[0]
    payload = post.call_args.kwargs["json"]
    assert url == "https://api.day.app/push"
    assert "secret-device-key" not in url
    assert payload["device_key"] == "secret-device-key"
    assert payload["url"] == "http://192.168.1.20:18000/orders"
    assert post.call_args.kwargs["timeout"] == 8


def test_bark_deduplicates_successful_delivery():
    values = _settings()
    key = "test:dedupe:unique"
    with patch("app.notifications.bark.get_setting", side_effect=_get_setting(values)), \
         patch("app.notifications.bark.requests.post", return_value=_response()) as post:
        first = bark.send("标题", "正文", dedupe_key=key)
        second = bark.send("标题", "正文", dedupe_key=key)

    assert first["skipped"] is False
    assert second["skipped"] is True
    assert post.call_count == 1


def test_pipeline_candidate_notification_explains_required_human_steps():
    values = _settings()
    values["execution.require_manual_confirmation"] = True
    with patch("app.notifications.bark.get_setting", side_effect=_get_setting(values)), \
         patch("app.notifications.bark.requests.post", return_value=_response()) as post:
        result = bark.notify_pipeline_result(
            run_id=901,
            status="done",
            result={"degraded": False},
            plans=[{
                "id": 1, "code": "600875", "name": "东方电气",
                "side": "buy", "max_buy_price": 28.5,
            }],
        )

    body = post.call_args.kwargs["json"]["body"]
    assert result["ok"] is True
    assert "核对公告" in body and "刷新价格门禁" in body
    assert "东方电气" in body and "28.50" in body


def test_pipeline_auto_ticket_notification_does_not_ask_for_approval():
    values = _settings()
    with patch("app.notifications.bark.get_setting", side_effect=_get_setting(values)), \
         patch("app.notifications.bark.requests.post", return_value=_response()) as post:
        result = bark.notify_pipeline_result(
            run_id=902,
            status="done",
            result={"degraded": False},
            plans=[{
                "id": 1, "code": "600875", "name": "东方电气",
                "side": "buy", "max_buy_price": 28.5,
            }],
        )

    payload = post.call_args.kwargs["json"]
    assert result["ok"] is True
    assert "模拟成交" in payload["title"]
    assert "自动成交" in payload["body"]
    assert "核对公告" not in payload["body"]


def test_monitor_delivery_failure_is_swallowed_and_does_not_mutate_event():
    values = _settings()
    event = SimpleNamespace(
        id=7, code="600000", name="测试股", pnl_pct=-0.16,
        trigger="deep_loss", action="review_required",
    )
    with patch("app.notifications.bark.get_setting", side_effect=_get_setting(values)), \
         patch("app.notifications.bark.requests.post", side_effect=OSError("offline")):
        result = bark.notify_monitor_event(event)

    assert result["ok"] is False
    assert event.action == "review_required"


def test_test_message_can_send_before_enabled_switch_is_on():
    values = _settings(enabled=False)
    with patch("app.notifications.bark.get_setting", side_effect=_get_setting(values)), \
         patch("app.notifications.bark.requests.post", return_value=_response()) as post:
        result = bark.send_test()

    assert result["ok"] is True
    assert post.call_count == 1
