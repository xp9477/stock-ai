import time
from functools import wraps


def ttl_cache(seconds: float):
    """简单内存 TTL 缓存,按位置参数 + 关键字参数作为 key。"""

    def decorator(fn):
        store: dict = {}

        @wraps(fn)
        def wrapper(*args, **kwargs):
            key = (args, tuple(sorted(kwargs.items())))
            hit = store.get(key)
            if hit is not None and time.time() - hit[0] < seconds:
                return hit[1]
            value = fn(*args, **kwargs)
            store[key] = (time.time(), value)
            return value

        wrapper.cache_clear = store.clear
        return wrapper

    return decorator
