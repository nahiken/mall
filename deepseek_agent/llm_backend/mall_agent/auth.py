"""#106：portal 会员 JWT 验签守卫（拒绝「带不匹配 token」的伪装；不拦截无 token 伪造）。

mall-agent 与 mall-portal 是受信内网两方。App 端登录后持 portal 会员 JWT
（HS256，claims 含 ``memberId``，见 mall-master mall-security JwtTokenUtil 重载），
请求 mall-agent 时经 ``Authorization: Bearer <token>`` 携带。当 PORTAL_JWT_SECRET
配置后，携带与 user_id 不匹配 token 的伪装请求被 403/401 拒绝；不带 token 的请求
按用户拍板决策降级放行（内网边界，R8 §3.2 风险部分收敛），因此本模块对无 token 的
伪造不构成拦截。

判定树（docs/reviews/r106-supply.md §3.3，用户拍板；标注「降级」即放行/不拦截分支）：
- ``PORTAL_JWT_SECRET`` 留空（未配置）→ 验签关闭，放行（内网 demo 边界，降级）。
- user_id 命中 ``PORTAL_ADMIN_USERS``（admin 端固定 'admin'，走 admin JWT，secret 与
  portal 不同无法交叉验签）→ 双通道放行（admin 会话为单固定演示用户，风险有界，降级）。
- 无 ``Authorization`` header → 降级放行（perf_test/loadtest/自测工具不带 token，降级）。
- header 非 ``Bearer <token>`` → 401。
- token 验签失败/过期/格式错 → 401。
- token 无 ``memberId`` claim（旧 token，签发早于 #106）→ 降级放行（真会员本人，降级）。
- ``str(memberId) == user_id`` → 放行。
- 否则（memberId 与 user_id 不匹配）→ 403。
"""

import logging

import jwt as pyjwt
from fastapi import HTTPException

from . import config

logger = logging.getLogger("mall_agent.auth")


class AuthDenied(Exception):
    """鉴权拒绝（HTTP 层翻译为 401/403；WS 层翻译为 error 帧 + close）。"""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def verify_authorization(authorization: str | None, claimed_user_id: str) -> str:
    """HTTP/WS 共用判定。返回放行时的有效 user_id；拒绝抛 AuthDenied。"""
    # 1. 验签关闭（未配置 secret）
    if not config.PORTAL_JWT_SECRET:
        return claimed_user_id
    # 2. admin 双通道：admin 端固定用户名（走 admin JWT，无法用 portal secret 验）
    if claimed_user_id in config.PORTAL_ADMIN_USERS:
        return claimed_user_id
    # 3. 无 header → 降级放行（工具链不带 token）
    if authorization is None or not authorization.strip():
        return claimed_user_id
    # 4. 非 Bearer 格式
    parts = authorization.strip().split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise AuthDenied(401, "Authorization header must be 'Bearer <token>'")
    token = parts[1].strip()
    if not token:
        raise AuthDenied(401, "Empty bearer token")
    # 5. 验签
    payload = _decode(token)
    member_id = payload.get("memberId")
    # 6. 旧 token（无 memberId claim）→ 降级放行
    if member_id is None:
        return claimed_user_id
    # 7. 比对
    if str(member_id) == claimed_user_id:
        return claimed_user_id
    logger.warning("user_id mismatch: token memberId=%s vs claimed=%s", member_id, claimed_user_id)
    raise AuthDenied(403, "user_id does not match the bearer token")


def verify_task_owner(authorization: str | None, owner_user_id: str) -> None:
    """P0-01：SSE 订阅 ``GET /chat/stream/{task_id}`` 的归属校验。

    与 ``verify_authorization``（提交态，允许无 token 降级）不同，订阅一个已存在
    的 task 必须能证明「你就是它的 owner」，否则 task_id 一旦出现在日志/分享链接里
    就能被他人旁路读取 AI 回复流。

    - 两个 JWT 密钥都未配置（纯本地 demo）→ 保持旧行为放行，并由调用方文档标注；
    - 无 Authorization / 非 Bearer → 401；
    - owner 是 admin 用户 → 优先用 ADMIN_JWT_SECRET 验 admin JWT，或 portal JWT；
    - 其他 → portal JWT 的 memberId 必须等于 owner，否则 403。
    """
    if not config.PORTAL_JWT_SECRET and not config.ADMIN_JWT_SECRET:
        return
    token = _bearer_token(authorization)
    if token is None:
        raise AuthDenied(401, "bearer token required to subscribe this task")

    if owner_user_id in config.PORTAL_ADMIN_USERS and config.ADMIN_JWT_SECRET:
        try:
            payload = pyjwt.decode(
                token,
                config.ADMIN_JWT_SECRET,
                algorithms=["HS256"],
                options={"verify_exp": True},
            )
            subject = payload.get("sub") or payload.get("username")
            if subject is not None and str(subject) == owner_user_id:
                return
        except pyjwt.PyJWTError:
            pass  # 继续尝试 portal 通道

    payload = _decode(token)  # 失败抛 401
    member_id = payload.get("memberId")
    if member_id is not None and str(member_id) == owner_user_id:
        return
    logger.warning("stream owner mismatch: task owner=%s vs token memberId=%s",
                   owner_user_id, member_id)
    raise AuthDenied(403, "task does not belong to the bearer token")


def _bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    parts = authorization.strip().split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip() or None


def verify_http(authorization: str | None, claimed_user_id: str) -> str:
    """HTTP handler 入口：拒绝时抛 FastAPI HTTPException。"""
    try:
        return verify_authorization(authorization, claimed_user_id)
    except AuthDenied as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


def _decode(token: str) -> dict:
    try:
        return pyjwt.decode(
            token,
            config.PORTAL_JWT_SECRET,
            algorithms=[config.PORTAL_JWT_ALGORITHM],
            options={"verify_exp": True},
        )
    except pyjwt.PyJWTError as exc:
        logger.info("portal JWT verify failed: %s", exc)
        raise AuthDenied(401, f"Invalid token: {exc}") from exc
