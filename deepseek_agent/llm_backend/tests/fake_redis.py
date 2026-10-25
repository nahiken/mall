"""进程内假 Redis（P2-01）。

只实现 redis_queue / stats 用到的命令子集，语义对齐 redis-py 的返回值
（SET NX 失败返回 None、XPENDING 明细是 list[dict] 等），这样队列逻辑可以在
**没有 Redis 服务**的机器上被真实执行到，而不是只做 import 级冒烟。

刻意不做的事：不模拟 TTL 过期、不模拟 PEL/XCLAIM 的时钟语义。需要这些的断言
放到 `_is_stale` / `_lease_alive` 这类纯判定函数上（见 test_queue.py），
避免用假实现去「证明」一个只有真 Redis 才能证明的结论。
"""


class FakeRedis:
    def __init__(self):
        self.kv: dict[str, str] = {}
        self.hashes: dict[str, dict] = {}
        self.streams: dict[str, list[tuple[str, dict]]] = {}
        self.expires: dict[str, int] = {}
        self.deleted: list[str] = []
        self.acked: list[tuple[str, str]] = []
        self._seq = 0

    # ── strings ─────────────────────────────────────────────────────────
    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.kv:
            return None
        self.kv[key] = value
        if ex is not None:
            self.expires[key] = ex
        return True

    async def get(self, key):
        return self.kv.get(key)

    async def delete(self, *keys):
        removed = 0
        for key in keys:
            self.deleted.append(key)
            if self.kv.pop(key, None) is not None:
                removed += 1
        return removed

    async def exists(self, key):
        return int(key in self.kv)

    # ── hashes ──────────────────────────────────────────────────────────
    async def hset(self, key, mapping=None, **kwargs):
        bucket = self.hashes.setdefault(key, {})
        bucket.update(mapping or {})
        bucket.update(kwargs)
        return len(bucket)

    async def hgetall(self, key):
        return dict(self.hashes.get(key, {}))

    async def expire(self, key, ttl):
        self.expires[key] = ttl
        return True

    # ── streams ─────────────────────────────────────────────────────────
    async def xadd(self, key, fields, maxlen=None, approximate=None, **kwargs):
        self._seq += 1
        msg_id = f"{self._seq}-0"
        self.streams.setdefault(key, []).append((msg_id, dict(fields)))
        return msg_id

    async def xrange(self, key, min="-", max="+", count=None):
        entries = list(self.streams.get(key, []))
        if min not in ("-", None):
            entries = [e for e in entries if e[0] == min]
        if count:
            entries = entries[:count]
        return entries

    async def xrevrange(self, key, max="+", min="-", count=None):
        entries = list(reversed(self.streams.get(key, [])))
        if count:
            entries = entries[:count]
        return entries

    async def xack(self, stream, group, *ids):
        for msg_id in ids:
            self.acked.append((stream, msg_id))
        return len(ids)

    async def aclose(self):
        self.closed = True
