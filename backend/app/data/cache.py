import time
from functools import wraps


def ttl_cache(seconds: float):
    """简单内存 TTL 缓存,按位置参数作为 key。"""

    def decorator(fn):
        store: dict = {}

        @wraps(fn)
        def wrapper(*args):
            key = args
            hit = store.get(key)
            if hit is not None and time.time() - hit[0] < seconds:
                return hit[1]
            value = fn(*args)
            store[key] = (time.time(), value)
            return value

        wrapper.cache_clear = store.clear
        return wrapper

    return decorator
