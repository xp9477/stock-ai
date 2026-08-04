"""扶摇客户端纯函数测试（无网络）。"""
from app.data.fuyao_client import from_thscode, to_thscode


def test_to_thscode_sh():
    assert to_thscode("600519") == "600519.SH"
    assert to_thscode("510300") == "510300.SH"


def test_to_thscode_sz():
    assert to_thscode("000001") == "000001.SZ"
    assert to_thscode("300750") == "300750.SZ"


def test_to_thscode_passthrough():
    assert to_thscode("600519.SH") == "600519.SH"


def test_from_thscode():
    assert from_thscode("600519.SH") == "600519"
