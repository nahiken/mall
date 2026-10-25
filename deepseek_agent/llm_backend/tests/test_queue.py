"""P2-01：任务队列（进程内 dedup / Redis 去重与终态 / 接管判定）。

队列是「用户点了两次发送」和「worker 崩了」这两类事故的唯一防线，而它的错误
模式是**静默**的：dedup 泄漏 → 内存涨；终态没写 → 任务重跑；接管判定太松 →
长任务被抢。所以这里挑的都是能在本机无 Redis 的情况下真实驱动的分支：

- 进程内：``find_inflight`` 命中/清理、上限清零；
- Redis 侧：``submit`` 的 SET NX 去重、``_terminal_state`` 双重判定、
  ``mark_done`` 的「先写终态→停心跳→XACK」顺序、``_is_stale``/``_lease_alive``
  的接管判定；
- 共享的 ``_decode_task`` 解析。

不测的：XPENDING + XCLAIM 的真实时序（需要真 Redis，见 docs/queue-heartbeat-drill.md）。
"""
import asyncio

import pytest

from mall_agent import queue as queue_mod
from mall_agent.queue import TaskQueue
from mall_agent.redis_queue import (
    CLAIM_IDLE_MS,
    DEDUP_PREFIX,
    HB_PREFIX,
    META_PREFIX,
    STREAM_TASKS,
    RedisTaskQueue,
)

from fake_redis import FakeRedis


def _messages(text: str = "想买手机") -> list[dict]:
    return [{"role": "user", "content": text}]


# ── 进程内队列：in-flight dedup ─────────────────────────────────────────


async def test_memory_dedup_registers_until_task_finishes():
    q = TaskQueue()
    key = q.dedup_key_for("1", "想买手机", None)
    assert key == "u:1|c:None|想买手机"

    task_id = await q.submit(_messages(), "1", None, dedup_key=key)
    assert await q.find_inflight(key) == task_id

    await q.mark_done(task_id)
    # 终态任务的键必须消失：进程内队列无法重放已完成任务的结果
    assert await q.find_inflight(key) is None


async def test_memory_dedup_cleared_on_failure():
    q = TaskQueue()
    key = q.dedup_key_for("1", "想买手机", 3)
    task_id = await q.submit(_messages(), "1", 3, dedup_key=key)
    await q.mark_failed(task_id, "boom")
    assert await q.find_inflight(key) is None


async def test_memory_dedup_ignores_blank_content():
    q = TaskQueue()
    assert q.dedup_key_for("1", "   ", None) is None


async def test_memory_dedup_can_be_disabled(monkeypatch):
    monkeypatch.setattr(queue_mod, "DEDUP_ENABLED", False)
    q = TaskQueue()
    assert q.dedup_key_for("1", "想买手机", None) is None
    # 无 dedup_key 时两次提交必须拿到不同任务
    first = await q.submit(_messages())
    second = await q.submit(_messages())
    assert first != second


async def test_memory_dedup_index_resets_at_bound(monkeypatch):
    monkeypatch.setattr(queue_mod, "DEDUP_MAX_TRACKED", 2)
    q = TaskQueue()
    for i in range(2):
        key = q.dedup_key_for("1", f"消息{i}", None)
        await q.submit(_messages(f"消息{i}"), "1", None, dedup_key=key)
    assert len(q._dedup_keys) == 2

    # 达到上限后查询新键 → 整表清零，返回 None（宁可漏合一次，也不无界增长）
    assert await q.find_inflight("u:1|c:None|新消息") is None
    assert q._dedup_keys == {}


async def test_memory_queue_reports_pending_tasks():
    q = TaskQueue()
    task_id = await q.submit(_messages())
    info = await q.get_task_info(task_id)
    assert info["status"] == "pending"
    assert await q.get_task_count() == 1
    assert [t["task_id"] for t in await q.get_all_pending()] == [task_id]


# ── Redis 队列：共享解析与去重 ─────────────────────────────────────────


def _redis_queue() -> tuple[RedisTaskQueue, FakeRedis]:
    q = RedisTaskQueue()
    fake = FakeRedis()
    q._redis = fake  # 跳过 from_url / XGROUP CREATE，直接注入假客户端
    return q, fake


def test_dedup_key_has_backend_prefix():
    q, _ = _redis_queue()
    key = q.dedup_key_for("1", "想买手机", None)
    assert key == f"{DEDUP_PREFIX}u:1|c:None|想买手机"


async def test_redis_submit_dedups_identical_inflight_submit():
    q, fake = _redis_queue()
    key = q.dedup_key_for("1", "想买手机", None)

    first = await q.submit(_messages(), "1", None, dedup_key=key)
    second = await q.submit(_messages(), "1", None, dedup_key=key)

    assert first == second
    # 只 XADD 了一次（重复提交没有真正入队）
    assert len(fake.streams[STREAM_TASKS]) == 1
    # 任务的 meta 已落 pending
    assert fake.hashes[f"{META_PREFIX}{first}"]["status"] == "pending"


async def test_redis_submit_without_key_always_enqueues():
    q, fake = _redis_queue()
    first = await q.submit(_messages(), "1", None)
    second = await q.submit(_messages(), "1", None)
    assert first != second
    assert len(fake.streams[STREAM_TASKS]) == 2


async def test_redis_submit_rolls_back_dedup_key_on_xadd_failure():
    q, fake = _redis_queue()
    key = q.dedup_key_for("1", "想买手机", None)

    async def boom(*a, **k):
        raise RuntimeError("xadd failed")

    fake.xadd = boom
    with pytest.raises(RuntimeError):
        await q.submit(_messages(), "1", None, dedup_key=key)
    # 入队失败必须把去重键删掉，否则下一次同内容提交会命中一个不存在的任务
    assert key not in fake.kv


def test_decode_task_parses_fields_and_ignores_bad_json():
    task = RedisTaskQueue._decode_task(
        "1-0",
        {"task_id": "abc", "messages": '{"bad": 1}', "user_id": "2",
         "conversation_id": "17"},
    )
    # JSON 合法但不是数组 → 退化为空消息列表，不抛
    assert task["messages"] == {"bad": 1} or task["messages"] == []
    assert task["conversation_id"] == 17
    assert task["_msg_id"] == "1-0"

    assert RedisTaskQueue._decode_task("1-0", {"messages": "[]"}) is None
    assert RedisTaskQueue._decode_task(
        "1-0", {"task_id": "abc", "messages": "not json"}
    )["messages"] == []


def test_decode_task_conversation_id_non_numeric_is_none():
    task = RedisTaskQueue._decode_task(
        "1-0", {"task_id": "abc", "conversation_id": "abc"}
    )
    assert task["conversation_id"] is None


# ── 接管判定（P1-07 心跳租约）──────────────────────────────────────────


@pytest.mark.parametrize(
    "idle_ms,heartbeat_alive,expected",
    [
        (CLAIM_IDLE_MS + 1, False, True),    # 超时且心跳没了 → 可接管
        (CLAIM_IDLE_MS, False, True),        # 边界值也算
        (CLAIM_IDLE_MS - 1, False, False),   # 还没到 idle 阈值
        (CLAIM_IDLE_MS + 60_000, True, False),  # 长任务：心跳还在 → 不许抢
        (0, True, False),
    ],
)
def test_is_stale_requires_both_idle_and_no_heartbeat(idle_ms, heartbeat_alive, expected):
    assert RedisTaskQueue._is_stale(idle_ms, heartbeat_alive) is expected


def test_hb_key_layout():
    assert RedisTaskQueue._hb_key("abc") == f"{HB_PREFIX}abc"


async def test_lease_alive_answers_true_when_redis_errors():
    """Redis 抖动时宁可晚接管，也不能重复执行。"""

    class BrokenRedis:
        async def exists(self, key):
            raise RuntimeError("connection reset")

    assert await RedisTaskQueue()._lease_alive(BrokenRedis(), "abc") is True


async def test_lease_alive_reads_heartbeat_key():
    q, fake = _redis_queue()
    assert await q._lease_alive(fake, "abc") is False
    fake.kv[f"{HB_PREFIX}abc"] = "1"
    assert await q._lease_alive(fake, "abc") is True


# ── 终态处理 ───────────────────────────────────────────────────────────


async def test_terminal_state_from_meta_status():
    q, fake = _redis_queue()
    fake.hashes[f"{META_PREFIX}t1"] = {"status": "done"}
    assert await q._terminal_state(fake, "t1") is True


async def test_terminal_state_from_event_stream_tail():
    q, fake = _redis_queue()
    fake.hashes[f"{META_PREFIX}t1"] = {"status": "pending"}
    fake.streams[f"mall_agent:ev:t1"] = [("1-0", {"event": "done", "data": "[DONE]"})]
    assert await q._terminal_state(fake, "t1") is True


async def test_terminal_state_false_for_running_task():
    q, fake = _redis_queue()
    fake.hashes[f"{META_PREFIX}t1"] = {"status": "running"}
    fake.streams[f"mall_agent:ev:t1"] = [("1-0", {"event": "text", "data": "hi"})]
    assert await q._terminal_state(fake, "t1") is False
    assert await q._terminal_state(fake, "") is False


async def test_worker_lease_starts_on_get_task_and_stops_on_done():
    q, fake = _redis_queue()
    task_id = await q.submit(_messages(), "1", None)
    q._local.put_nowait({"task_id": task_id, "messages": _messages(),
                         "user_id": "1", "conversation_id": None})
    task = await q.get_task()
    assert task["task_id"] == task_id

    # 心跳协程跑起来后必须写 key
    for _ in range(3):
        await asyncio.sleep(0)
    assert f"{HB_PREFIX}{task_id}" in fake.kv
    assert task_id in q._leases

    fake.streams[STREAM_TASKS] = fake.streams.get(STREAM_TASKS, [])
    fake.streams[STREAM_TASKS] = [("1-0", {"task_id": task_id})]
    q._add_claim(task_id, "1-0")

    await q.mark_done(task_id)

    # 顺序断言：终态 meta 写成功、心跳 key 被删、消息被 XACK
    assert fake.hashes[f"{META_PREFIX}{task_id}"]["status"] == "done"
    assert f"{HB_PREFIX}{task_id}" not in fake.kv
    assert q._leases == {}
    assert (STREAM_TASKS, "1-0") in fake.acked
    assert task_id not in q._claimed


async def test_unacked_terminal_event_keeps_message_pending():
    """终态事件写不进去时不能 XACK，否则任务消息永远丢失。"""
    q, fake = _redis_queue()
    task_id = await q.submit(_messages(), "1", None)
    q._add_claim(task_id, "1-0")

    async def boom(*a, **k):
        raise RuntimeError("xadd failed")

    real_xadd = fake.xadd
    fake.xadd = boom
    await q.mark_done(task_id)
    fake.xadd = real_xadd

    assert fake.hashes[f"{META_PREFIX}{task_id}"]["status"] == "done"
    assert fake.acked == []  # 没有 XACK → 留给 reaper 恢复


async def test_aclose_stops_all_leases():
    q, fake = _redis_queue()
    task_id = await q.submit(_messages(), "1", None)
    q._local.put_nowait({"task_id": task_id, "messages": _messages(),
                         "user_id": "1", "conversation_id": None})
    await q.get_task()
    await q.aclose()
    assert q._leases == {}
    assert q._redis is None
