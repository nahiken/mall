"""Mall Agent API router.

Endpoints
---------
POST    /api/mall/chat               submit → immediate {task_id, conversation_id}
POST    /api/mall/chat/stream        [legacy] submit + SSE in one request
GET     /api/mall/chat/stream/{id}   SSE subscribe by task_id
WS      /api/mall/chat/ws            WebSocket bidirectional

GET     /api/mall/conversations               list user conversations (paginated)
GET     /api/mall/conversations/{id}           get conversation detail
GET     /api/mall/conversations/{id}/messages  list messages (paginated, oldest-first)
DELETE  /api/mall/conversations/{id}           delete conversation
PUT     /api/mall/conversations/{id}/title     update title

GET     /api/mall/queue/status       queue depth & worker info
GET     /api/mall/health             health check
"""
import json
import logging
from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect, Query, Request, HTTPException
from pydantic import BaseModel, Field
from starlette.responses import StreamingResponse

from .service import process_messages
from .queue import TaskQueue
from .config import WORKER_COUNT
from . import history_service as hs
from . import auth
from .monitor_auth import require_monitor

logger = logging.getLogger("mall_agent.router")

router = APIRouter()

# Will be set by run_mall.py on startup
task_queue: TaskQueue | None = None
worker_count: int = WORKER_COUNT


# ── request / response models ───────────────────────────────────────────

class ChatMessage(BaseModel):
    """P0-06：单条消息有长度上限，避免一次请求塞进超长文本烧 token。"""
    role: str = Field(min_length=1, max_length=32)
    content: str = Field(min_length=1, max_length=4000)


class ChatRequest(BaseModel):
    """P0-06：轮数与 user_id 都设上限；超限由 FastAPI 返回 422（不进入队列）。"""
    messages: list[ChatMessage] = Field(min_length=1, max_length=50)
    user_id: str = Field(min_length=1, max_length=64)
    conversation_id: int | None = None


class SubmitResponse(BaseModel):
    task_id: str
    conversation_id: int
    status: str = "pending"
    stream_url: str = ""


class ConversationListItem(BaseModel):
    id: int
    title: str
    message_count: int
    created_at: str
    updated_at: str


class MessageItem(BaseModel):
    id: int
    role: str
    content: str
    msg_type: str
    extra: dict | None = None
    created_at: str


class PaginatedResponse(BaseModel):
    items: list
    total: int
    page: int
    page_size: int
    total_pages: int


class UpdateTitleRequest(BaseModel):
    title: str


# ── helpers ─────────────────────────────────────────────────────────────

def _get_queue() -> TaskQueue:
    if task_queue is None:
        raise RuntimeError("TaskQueue not initialised")
    return task_queue


def _last_user_content(messages: list) -> str:
    """The content of the most recent user message, or '' if none."""
    for m in reversed(messages):
        if isinstance(m, ChatMessage):
            role, content = m.role, m.content
        else:
            role, content = m.get("role"), m.get("content", "")
        if role == "user":
            return content or ""
    return ""


def _http_authorization(request: Request) -> str | None:
    """Extract the raw Authorization header (None when absent)."""
    return request.headers.get("authorization")


async def _relay_to_sse(task_id: str):
    """Async generator: subscribe to task events → yield SSE lines."""
    q = _get_queue()
    async for event_type, data_str in q.subscribe(task_id):
        yield f"event: {event_type}\ndata: {data_str}\n\n"


async def _ensure_conversation(user_id: str, conversation_id: int | None,
                                user_content: str) -> int:
    """Get or create conversation. Save user message. Returns conversation_id."""
    if conversation_id:
        conv = await hs.get_conversation(conversation_id, user_id)
        if conv is None:
            raise HTTPException(status_code=404, detail="Conversation not found")
    else:
        # Auto-title: first 30 chars of user message
        title = (user_content[:30] + "...") if len(user_content) > 30 else user_content
        conversation_id = await hs.create_conversation(user_id, title=title or "新对话")

    # Save user message
    await hs.save_message(conversation_id, "user", user_content)
    return conversation_id


# ── chat endpoints ──────────────────────────────────────────────────────

async def _submit_with_dedup(q: TaskQueue, user_id: str, conversation_id: int | None,
                             user_content: str, raw_messages: list) -> tuple[str, int]:
    """Dedup-aware submit. Returns (task_id, conversation_id).

    When the identical (user_id, request conversation_id, last user content) is
    already in flight, reuse its task_id and conversation_id instead of saving a
    duplicate user row + enqueuing a duplicate task (which would stream a second,
    identical reply and leave an unreplied orphan user message in history). On a
    miss, ensure the conversation (saving the user turn) and submit fresh.

    Scope is this process / in-flight only (see config.DEDUP_*); a completed
    task is not replayed — a genuine re-ask after completion is a new turn.
    """
    dedup_key = q.dedup_key_for(user_id, user_content, conversation_id)
    if dedup_key is not None:
        existing = await q.find_inflight(dedup_key)
        if existing is not None:
            info = await q.get_task_info(existing) or {}
            conv_id = info.get("conversation_id") or conversation_id or 0
            logger.info("Dedup hit for user=%s conv=%s: reuse task %s", user_id, conv_id, existing)
            return existing, int(conv_id)

    conv_id = await _ensure_conversation(user_id, conversation_id, user_content)
    task_id = await q.submit(raw_messages, user_id, conversation_id=conv_id, dedup_key=dedup_key)
    return task_id, conv_id


@router.post("/chat", response_model=SubmitResponse)
async def submit_chat(req: ChatRequest, request: Request):
    """Submit a chat message. Returns immediately with task_id + conversation_id.

    Client then opens GET /api/mall/chat/stream/{task_id} for SSE results,
    or uses the WebSocket endpoint. #105: an in-flight identical resubmit
    (double-click / retry / SSE-reconnect replay) reuses the same task_id.
    #106: verifies the portal member JWT (when configured) against the claimed
    user_id (see mall_agent.auth).
    """
    auth.verify_http(_http_authorization(request), req.user_id)
    q = _get_queue()
    user_content = _last_user_content(req.messages)
    raw = [{"role": m.role, "content": m.content} for m in req.messages]
    task_id, conv_id = await _submit_with_dedup(q, req.user_id, req.conversation_id,
                                                user_content, raw)

    return SubmitResponse(
        task_id=task_id,
        conversation_id=conv_id,
        status="pending",
        stream_url=f"/api/mall/chat/stream/{task_id}",
    )


@router.post("/chat/stream")
async def chat_stream_legacy(req: ChatRequest, request: Request):
    """[Legacy] Submit + SSE streaming in one request.

    Kept for backward compatibility. Also saves history. #105: dedups an
    in-flight identical resubmit (same task_id streamed again). #106: same
    portal-JWT verification as POST /chat.
    """
    auth.verify_http(_http_authorization(request), req.user_id)
    q = _get_queue()
    user_content = _last_user_content(req.messages)
    raw = [{"role": m.role, "content": m.content} for m in req.messages]
    task_id, conv_id = await _submit_with_dedup(q, req.user_id, req.conversation_id,
                                                user_content, raw)

    async def _stream_with_conv():
        # Emit meta event so SSE client gets conversation_id
        yield f"event: meta\ndata: {json.dumps({'conversation_id': conv_id}, ensure_ascii=False)}\n\n"
        async for line in _relay_to_sse(task_id):
            yield line

    return StreamingResponse(
        _stream_with_conv(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/chat/stream/{task_id}")
async def stream_task_result(task_id: str, request: Request):
    """SSE stream for an already-submitted task.

    P0-01: subscribing an existing task requires proving ownership (bearer JWT
    memberId must equal the task's user_id); unknown task_id → 404. 本地未配置
    JWT 密钥时保持旧行为（见 ``auth.verify_task_owner``）。
    """
    q = _get_queue()
    info = await q.get_task_info(task_id)
    if info is None:
        raise HTTPException(status_code=404, detail="Task not found")
    try:
        auth.verify_task_owner(_http_authorization(request), str(info.get("user_id") or ""))
    except auth.AuthDenied as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    return StreamingResponse(
        _relay_to_sse(task_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.websocket("/chat/ws")
async def websocket_chat(ws: WebSocket):
    """WebSocket bidirectional chat with streaming results.

    NOTE (#105): this path intentionally does NOT dedup. A dedup hit would make
    a second connection subscribe to the SAME per-task event queue, and two
    subscribers consuming one asyncio.Queue race each other for events (each
    event goes to exactly one consumer) — both ends would receive a partial
    stream. That model only works with a single subscriber per task (POST /chat
    + its one SSE GET). Cross-connection result sharing is a Redis-bus concern
    (#104), not an in-memory one. So: each WS send is a fresh task.
    """
    await ws.accept()
    q = _get_queue()

    try:
        raw = await ws.receive_json()
        messages_raw = raw.get("messages", [])
        user_id = str(raw.get("user_id") or "").strip()
        if not user_id:
            await ws.send_json({"event": "error", "data": {"message": "请先登录"}})
            await ws.close(code=1008)
            return
        # #106：WS 握手头带 portal JWT 时校验（浏览器 WS 不便带头，无头降级放行）。
        try:
            auth.verify_authorization(ws.headers.get("authorization"), user_id)
        except auth.AuthDenied as exc:
            await ws.send_json({"event": "error", "data": {"message": exc.detail}})
            await ws.close(code=1008)
            return
        conversation_id = raw.get("conversation_id")

        user_content = _last_user_content(messages_raw)

        conv_id = await _ensure_conversation(user_id, conversation_id, user_content)
        task_id = await q.submit(messages_raw, user_id, conversation_id=conv_id)

        # Send back the conversation_id so the client can store it
        await ws.send_json({"event": "meta", "data": {
            "conversation_id": conv_id,
            "task_id": task_id,
        }})

        async for event_type, data_str in q.subscribe(task_id):
            try:
                data_obj = json.loads(data_str) if data_str.startswith("{") else data_str
            except json.JSONDecodeError:
                data_obj = data_str
            await ws.send_json({"event": event_type, "data": data_obj})

    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected")
    except Exception as exc:
        logger.exception("WebSocket error")
        try:
            await ws.send_json({"event": "error", "data": {"message": str(exc)}})
        except Exception:
            pass


# ── conversation history endpoints ───────────────────────────────────────

@router.get("/conversations", response_model=PaginatedResponse)
async def list_user_conversations(
    request: Request,
    user_id: str = Query(min_length=1),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    """List conversations for a user, newest first. #106: verifies the bearer
    JWT (when configured) against the claimed user_id."""
    auth.verify_http(_http_authorization(request), user_id)
    items, total = await hs.list_conversations(user_id, page, page_size)
    return PaginatedResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=max(1, (total + page_size - 1) // page_size),
    )


@router.post("/conversations/hide")
async def hide_user_history(request: Request, user_id: str = Query(min_length=1)):
    """Hide all existing history for a user without deleting database rows."""
    auth.verify_http(_http_authorization(request), user_id)
    hidden_before = await hs.hide_history(user_id)
    return {"message": "hidden", "hidden_before": hidden_before.isoformat()}


@router.get("/conversations/{conversation_id}")
async def get_conversation(
    request: Request,
    conversation_id: int,
    user_id: str = Query(min_length=1),
):
    """Get a single conversation owned by the user."""
    auth.verify_http(_http_authorization(request), user_id)
    conv = await hs.get_conversation(conversation_id, user_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conv


@router.get("/conversations/{conversation_id}/messages", response_model=PaginatedResponse)
async def list_conversation_messages(
    request: Request,
    conversation_id: int,
    user_id: str = Query(min_length=1),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    """List messages in a conversation, oldest-first with pagination.

    page=1 returns the MOST RECENT messages; higher pages go further back.
    This matches the UX: on page load, show latest messages; scroll up → older.
    """
    auth.verify_http(_http_authorization(request), user_id)
    conv = await hs.get_conversation(conversation_id, user_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    items, total = await hs.list_messages(conversation_id, page, page_size)
    return PaginatedResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=max(1, (total + page_size - 1) // page_size),
    )


@router.delete("/conversations/{conversation_id}")
async def delete_user_conversation(
    request: Request,
    conversation_id: int,
    user_id: str = Query(min_length=1),
):
    """Delete a conversation owned by the user and all its messages."""
    auth.verify_http(_http_authorization(request), user_id)
    conv = await hs.get_conversation(conversation_id, user_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    await hs.delete_conversation(conversation_id)
    return {"message": "deleted"}


@router.put("/conversations/{conversation_id}/title")
async def update_conversation_title(
    request: Request,
    conversation_id: int,
    req: UpdateTitleRequest,
    user_id: str = Query(min_length=1),
):
    """Update a conversation title owned by the user."""
    auth.verify_http(_http_authorization(request), user_id)
    conv = await hs.get_conversation(conversation_id, user_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    await hs.update_conversation_title(conversation_id, req.title)
    return {"message": "ok"}


# ── system endpoints ────────────────────────────────────────────────────

@router.get("/queue/status")
async def queue_status(_: None = Depends(require_monitor)):
    """Return queue depth, worker info, and recent task identifiers.

    P0-01: 管理面鉴权（见 ``monitor_auth``）。返回体只保留 task_id/status/
    created_at —— 原实现把 user_id、conversation_id 一并吐出，等于匿名泄露
    用户活动与会话关系。
    """
    q = _get_queue()
    pending = await q.get_all_pending()
    return {
        "queue_depth": len(pending),
        "workers": worker_count,
        "pending_tasks": [
            {
                "task_id": p.get("task_id"),
                "status": p.get("status"),
                "created_at": p.get("created_at"),
            }
            for p in pending[:20]
        ],
    }


@router.get("/health")
async def health():
    return {"status": "ok", "service": "mall-agent"}
