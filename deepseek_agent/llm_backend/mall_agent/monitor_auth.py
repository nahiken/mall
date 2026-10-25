"""P0-01：管理面鉴权依赖（/api/monitor/*、/queue/status）。

背景：这些端点原先全部匿名可读，``POST /api/monitor/rag`` 甚至能被任意人关掉
知识库检索；``/queue/status`` 还会泄露任务的 user_id / conversation_id。

通道（任一通过即可，见 ``config.MONITOR_*``）：

1. ``X-Monitor-Token: <MONITOR_TOKEN>`` — 运维脚本 / curl 用；
2. ``Authorization: Bearer <admin JWT>`` — admin 前端复用登录态，用
   ``ADMIN_JWT_SECRET``（mall-admin 的 jwt.secret）验签，sub 须在
   ``MONITOR_ADMIN_USERS`` 内；
3. ``Authorization: Bearer <portal JWT>`` — 用 ``PORTAL_JWT_SECRET`` 验签，
   memberId 须在 ``PORTAL_ADMIN_USERS`` 内（既有 admin 双通道）。

``MONITOR_AUTH=off`` 仅用于本地排障回滚，等价恢复匿名；生产保持默认 on。
未配置任何凭据时 fail-closed（401），不静默放行。

Token 比较使用 :func:`hmac.compare_digest`，避免逐字节比较的时序侧信道。
"""

import hmac
import logging

import jwt as pyjwt
from fastapi import HTTPException, Request

from . import config

logger = logging.getLogger("mall_agent.monitor_auth")


def _bearer_token(authorization: str | None) -> str | None:
    """从 ``Authorization: Bearer <token>`` 取 token；格式不符返回 None。"""
    if not authorization:
        return None
    parts = authorization.strip().split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip() or None


def _decode(token: str, secret: str) -> dict | None:
    try:
        return pyjwt.decode(token, secret, algorithms=["HS256"],
                            options={"verify_exp": True})
    except pyjwt.PyJWTError as exc:
        logger.info("monitor JWT rejected: %s", exc)
        return None


def _admin_jwt_ok(token: str) -> bool:
    if not config.ADMIN_JWT_SECRET:
        return False
    payload = _decode(token, config.ADMIN_JWT_SECRET)
    if payload is None:
        return False
    # hutool 默认把用户名放 sub；兼容 username 键。
    subject = payload.get("sub") or payload.get("username")
    return bool(subject) and str(subject) in config.MONITOR_ADMIN_USERS


def _portal_admin_jwt_ok(token: str) -> bool:
    if not config.PORTAL_JWT_SECRET:
        return False
    payload = _decode(token, config.PORTAL_JWT_SECRET)
    if payload is None:
        return False
    member_id = payload.get("memberId")
    return member_id is not None and str(member_id) in config.PORTAL_ADMIN_USERS


def monitor_authenticated(request: Request) -> bool:
    """只判定、不抛异常（供 SSE 等需要自定义错误体的场景复用）。"""
    if not config.MONITOR_AUTH:
        return True
    presented = (request.headers.get("x-monitor-token") or "").strip()
    if presented and config.MONITOR_TOKEN and hmac.compare_digest(
            presented, config.MONITOR_TOKEN):
        return True
    token = _bearer_token(request.headers.get("authorization"))
    if token is None:
        return False
    return _admin_jwt_ok(token) or _portal_admin_jwt_ok(token)


async def require_monitor(request: Request) -> None:
    """FastAPI 依赖：未认证抛 401（带 WWW-Authenticate，便于前端区分登录态失效）。"""
    if monitor_authenticated(request):
        return
    raise HTTPException(
        status_code=401,
        detail="monitor authentication required",
        headers={"WWW-Authenticate": "Bearer"},
    )
