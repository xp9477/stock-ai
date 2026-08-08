"""对无原生 timeout 的阻塞调用加线程超时（新浪 AKShare 等）。"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from typing import Any, Callable, TypeVar

T = TypeVar("T")

_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="ds-timeout")


def call_with_timeout(fn: Callable[..., T], timeout_sec: float, *args: Any, **kwargs: Any) -> T:
    """在 timeout_sec 内跑完 fn，否则抛 TimeoutError。

    注意：超时后底层线程可能仍在跑（无法强杀），仅保证调用方不再阻塞。
    """
    if timeout_sec is None or timeout_sec <= 0:
        return fn(*args, **kwargs)
    fut = _pool.submit(fn, *args, **kwargs)
    try:
        return fut.result(timeout=float(timeout_sec))
    except FuturesTimeout as err:
        raise TimeoutError(f"调用超时（{timeout_sec}s）: {getattr(fn, '__name__', fn)}") from err
