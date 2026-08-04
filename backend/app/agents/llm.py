"""OpenAI 兼容 LLM 客户端,带重试与 JSON 解析容错。"""
import json
import logging
import re
import time

from openai import OpenAI

from ..runtime_settings import get_setting

logger = logging.getLogger(__name__)

_client: OpenAI | None = None
_client_fp: str | None = None  # base_url + key 指纹，变更则重建


def reset_client() -> None:
    global _client, _client_fp
    _client = None
    _client_fp = None


def get_client() -> OpenAI:
    global _client, _client_fp
    base = str(get_setting("secrets.llm_base_url")).strip()
    key = str(get_setting("secrets.llm_api_key")).strip()
    fp = f"{base}|{key}"
    if _client is None or _client_fp != fp:
        _client = OpenAI(base_url=base, api_key=key or "EMPTY")
        _client_fp = fp
    return _client


def chat(system: str, user: str, model: str, retries: int = 2) -> str:
    """调用指定模型,失败重试 retries 次,最终失败抛出异常。"""
    last_err: Exception | None = None
    temp = float(get_setting("secrets.llm_temperature"))
    for attempt in range(retries + 1):
        try:
            # 指令并入 user 消息:部分中转端点会丢弃/覆盖 system 消息
            resp = get_client().chat.completions.create(
                model=model,
                temperature=temp,
                messages=[
                    {"role": "user",
                     "content": f"【你的角色与任务】\n{system}\n\n【输入材料】\n{user}"},
                ],
            )
            return resp.choices[0].message.content or ""
        except Exception as err:  # noqa: BLE001
            last_err = err
            logger.warning("LLM 调用失败 (第 %d 次): %s", attempt + 1, err)
            if attempt < retries:
                time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"LLM 调用失败: {last_err}")


def decide_with_fallback(output: str, model: str) -> dict | None:
    """解析决策 JSON;失败时追加一次轻量调用让模型提取结论。"""
    decision = parse_decision_json(output)
    if decision is not None:
        return decision
    from . import prompts

    try:
        extracted = chat(prompts.JSON_EXTRACT, output[-3000:], model, retries=1)
        return parse_decision_json(extracted)
    except Exception as err:  # noqa: BLE001
        logger.warning("JSON 提取兜底失败: %s", err)
        return None


def parse_decision_json(text: str) -> dict | None:
    """从 LLM 输出中提取决策 JSON,失败返回 None。"""
    data = None
    for raw in reversed(re.findall(r"\{[^{}]*\}", text, re.DOTALL)):
        try:
            candidate = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(candidate, dict) and "action" in candidate:
            data = candidate
            break
    if data is None:
        return None
    action = str(data.get("action", "")).lower()
    if action not in ("buy", "sell", "hold"):
        return None
    try:
        target = float(data.get("target_position_pct", 0))
        confidence = float(data.get("confidence", 0))
    except (TypeError, ValueError):
        return None
    target = min(max(target, 0.0), 1.0)
    confidence = min(max(confidence, 0.0), 1.0)
    return {
        "action": action,
        "target_position_pct": target,
        "confidence": confidence,
        "reason": str(data.get("reason", "")),
    }
