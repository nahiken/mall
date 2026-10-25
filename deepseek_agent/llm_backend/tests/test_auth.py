"""P2-01：portal 会员 JWT 验签矩阵（auth.py 判定树逐分支覆盖）。

auth.py 的文档字符串写了一张判定树；这里是它的可执行版本。每个用例只断言
「放行 / 401 / 403」这三态，不依赖真实 portal 服务 —— 签名用同一套 HS256 规则
本地签发即可。

为什么值得单独测：这条链路是 P0 之前唯一的跨用户越权入口（撞库拿到别人的
user_id 就能读别人的对话）。判定树的分支多且互相短路，改一处很容易把某个
「降级放行」分支变成默认拒绝（把 perf_test、admin 页面全打死）或反过来。
"""
import time

import jwt as pyjwt
import pytest
from fastapi import HTTPException

from mall_agent import config
from mall_agent.auth import (
    AuthDenied,
    verify_authorization,
    verify_http,
    verify_task_owner,
)

SECRET = "unit-test-portal-secret"


def _token(secret: str = SECRET, exp_delta: int = 60, **claims) -> str:
    payload = {"memberId": 1, "exp": int(time.time()) + exp_delta}
    payload.update(claims)
    return pyjwt.encode(payload, secret, algorithm="HS256")


@pytest.fixture
def portal_secret(monkeypatch):
    monkeypatch.setattr(config, "PORTAL_JWT_SECRET", SECRET)
    monkeypatch.setattr(config, "PORTAL_ADMIN_USERS", {"admin"})
    return SECRET


# ── 提交态（HTTP / WS 入口）─────────────────────────────────────────────


def test_no_secret_configured_allows_everything(monkeypatch):
    monkeypatch.setattr(config, "PORTAL_JWT_SECRET", "")
    assert verify_authorization("Bearer garbage", "7") == "7"


def test_missing_secret_ignores_admin_check(monkeypatch):
    """未配置 secret 时连 admin 分支都不需要（第一层就放行）。"""
    monkeypatch.setattr(config, "PORTAL_JWT_SECRET", "")
    monkeypatch.setattr(config, "PORTAL_ADMIN_USERS", {"admin"})
    assert verify_authorization(None, "admin") == "admin"


def test_admin_user_bypasses_portal_verification(portal_secret):
    """admin 端走 admin JWT，其 secret 与 portal 不同 → 必须整支跳过验签。"""
    assert verify_authorization("Bearer not-a-portal-token", "admin") == "admin"


def test_no_header_degrades_to_allow(portal_secret):
    assert verify_authorization(None, "7") == "7"


def test_blank_header_degrades_to_allow(portal_secret):
    assert verify_authorization("   ", "7") == "7"


@pytest.mark.parametrize("header", ["Basic abc", "Bearer", "Bearer    ", "token-only"])
def test_non_bearer_format_is_401(portal_secret, header):
    with pytest.raises(AuthDenied) as exc:
        verify_authorization(header, "7")
    assert exc.value.status_code == 401


@pytest.mark.parametrize(
    "token",
    [
        "not-a-jwt",
        _token(secret="wrong-secret"),          # 签名不对
        _token(exp_delta=-10),                  # 已过期
    ],
)
def test_invalid_token_is_401(portal_secret, token):
    with pytest.raises(AuthDenied) as exc:
        verify_authorization(f"Bearer {token}", "7")
    assert exc.value.status_code == 401


def test_matching_member_id_is_allowed(portal_secret):
    assert verify_authorization(f"Bearer {_token(memberId=7)}", "7") == "7"


def test_member_id_compared_as_string(portal_secret):
    """portal 签发的 memberId 是数字，请求里的 user_id 是字符串。"""
    assert verify_authorization(f"Bearer {_token(memberId=42)}", "42") == "42"


def test_mismatched_member_id_is_403(portal_secret):
    with pytest.raises(AuthDenied) as exc:
        verify_authorization(f"Bearer {_token(memberId=99)}", "7")
    assert exc.value.status_code == 403


def test_old_token_without_member_id_claim_degrades(portal_secret):
    """签发早于 #106 的旧 token 没有 memberId，属于「真会员本人」→ 放行。"""
    old = pyjwt.encode(
        {"sub": "7", "exp": int(time.time()) + 60}, SECRET, algorithm="HS256"
    )
    assert verify_authorization(f"Bearer {old}", "7") == "7"


def test_http_wrapper_translates_to_http_exception(portal_secret):
    with pytest.raises(HTTPException) as exc:
        verify_http(f"Bearer {_token(memberId=99)}", "7")
    assert exc.value.status_code == 403


# ── SSE 订阅态（必须证明是 owner）───────────────────────────────────────


def test_owner_check_open_when_no_secrets(monkeypatch):
    monkeypatch.setattr(config, "PORTAL_JWT_SECRET", "")
    monkeypatch.setattr(config, "ADMIN_JWT_SECRET", "")
    verify_task_owner(None, "7")  # 不抛即通过


def test_owner_check_requires_token_when_secret_set(portal_secret, monkeypatch):
    monkeypatch.setattr(config, "ADMIN_JWT_SECRET", "admin-secret")
    with pytest.raises(AuthDenied) as exc:
        verify_task_owner(None, "7")
    assert exc.value.status_code == 401


def test_owner_check_accepts_matching_portal_token(portal_secret, monkeypatch):
    monkeypatch.setattr(config, "ADMIN_JWT_SECRET", "")
    verify_task_owner(f"Bearer {_token(memberId=7)}", "7")


def test_owner_check_rejects_other_members(portal_secret, monkeypatch):
    monkeypatch.setattr(config, "ADMIN_JWT_SECRET", "")
    with pytest.raises(AuthDenied) as exc:
        verify_task_owner(f"Bearer {_token(memberId=99)}", "7")
    assert exc.value.status_code == 403


def test_owner_check_admin_token_via_admin_secret(portal_secret, monkeypatch):
    monkeypatch.setattr(config, "ADMIN_JWT_SECRET", "admin-secret")
    admin_token = pyjwt.encode(
        {"sub": "admin", "exp": int(time.time()) + 60}, "admin-secret", algorithm="HS256"
    )
    verify_task_owner(f"Bearer {admin_token}", "admin")


def test_owner_check_admin_token_wrong_subject_falls_through_to_401(
    portal_secret, monkeypatch
):
    """admin 通道验签过但 sub 不匹配 → 继续用 portal secret 解，必然失败 → 401。"""
    monkeypatch.setattr(config, "ADMIN_JWT_SECRET", "admin-secret")
    admin_token = pyjwt.encode(
        {"sub": "someone-else", "exp": int(time.time()) + 60},
        "admin-secret",
        algorithm="HS256",
    )
    with pytest.raises(AuthDenied) as exc:
        verify_task_owner(f"Bearer {admin_token}", "admin")
    assert exc.value.status_code == 401


def test_owner_check_admin_owner_accepts_portal_token(portal_secret, monkeypatch):
    """admin 用户也可能持 portal token（memberId 命中 admin 名单）。"""
    monkeypatch.setattr(config, "ADMIN_JWT_SECRET", "")
    verify_task_owner(f"Bearer {_token(memberId='admin')}", "admin")
