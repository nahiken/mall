"""Mall Agent configuration loaded from environment."""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

# Deepseek API
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

# Java Mall Backend
MALL_API_BASE = os.getenv("MALL_API_BASE", "http://localhost:8085")

# ── P1-02：Agent 商品检索走 ES ───────────────────────────────────────────
# mall-search 的 /esProduct/search（0-based pageNum）。ES 不可用（连接失败/超时/
# 非 200）时自动回退 portal /product/search，日志可见。回滚：MALL_AGENT_SEARCH_ES=off。
SEARCH_API_BASE = os.getenv("SEARCH_API_BASE", "http://127.0.0.1:8081")
SEARCH_ES_ENABLED = os.getenv("MALL_AGENT_SEARCH_ES", "on").strip().lower() not in ("", "0", "false", "off")
SEARCH_TIMEOUT_SECONDS = float(os.getenv("SEARCH_TIMEOUT_SECONDS", "2"))

# Server
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8001"))

# MySQL Database
DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "port": int(os.getenv("DB_PORT", "3306")),
    "user": os.getenv("DB_USER", "root"),
    "password": os.getenv("DB_PASSWORD", ""),
    "database": os.getenv("DB_NAME", "mall"),
}

# Background workers (how many concurrent LLM calls to allow)
WORKER_COUNT = int(os.getenv("WORKER_COUNT", "4"))

# DB connection pool (per-process, held by the async engine). Tuned so a higher
# WORKER_COUNT / more concurrent history calls don't starve on the 5+10 default.
DB_POOL_SIZE = int(os.getenv("DB_POOL_SIZE", "5"))
DB_POOL_MAX_OVERFLOW = int(os.getenv("DB_POOL_MAX_OVERFLOW", "10"))

# In-flight deduplication of identical chat submits (local, single-process only).
# Deduplicates a client retry / double-click / SSE-reconnect resubmit whose
# (user_id, last-user-content) matches a task that is STILL PENDING or RUNNING,
# returning the existing task_id instead of enqueuing a duplicate. Scope is
# deliberately limited to this process — it is NOT a cross-instance/distributed
# dedup (that needs a shared store, see PROD-HIGH-CONCURRENCY-DESIGN §5 阶段1 /
# #104 Redis bus). Rollback: MALL_AGENT_DEDUP=off → always submit a fresh task.
DEDUP_ENABLED = os.getenv("MALL_AGENT_DEDUP", "on").strip().lower() not in ("", "0", "false", "off")
# Upper bound on tracked in-flight tasks per process (LRU-ish eviction guard).
DEDUP_MAX_TRACKED = int(os.getenv("MALL_AGENT_DEDUP_MAX_TRACKED", "512"))

# Task-queue backend. "redis" uses the Redis Streams bus (see redis_queue.py —
# multi-process/multi-instance task distribution, see PROD-HIGH-CONCURRENCY-DESIGN
# §4.2 阶段3 / #104); "memory" (default) keeps the in-process asyncio.Queue
# implementation. Rollback: QUEUE_BACKEND=memory.
QUEUE_BACKEND = os.getenv("QUEUE_BACKEND", "memory").strip().lower()

# Redis connection (used when QUEUE_BACKEND=redis).
REDIS_URL = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")

# Per-turn decision-flow tracing. "on" emits an extra ``stage`` SSE event for
# each decision node the agent enters (see flow.py); "off" keeps the legacy
# event stream (intent/text/products/…) with zero extra events. flow.py binds
# this value at import, so it takes effect only at process start — it is a
# 启动期开关（重启生效）, not a runtime toggle.
NODE_TRACE = os.getenv("MALL_AGENT_NODE_TRACE", "on") == "on"

# ── P1-01：意图合并 / 规则短路 / LLM 超时重试 ────────────────────────────
# on（默认）：一次 LLM 调用同时产出 intent/category/keywords/terms，推荐回合
# 从 3 次 LLM（意图 + 检索词扩展 + 回答）降到 2 次；off：回退旧的两次调用
# （classify_intent + _llm_expand_search_terms），用于对比与回滚。
FUSED_INTENT = os.getenv("MALL_AGENT_FUSED_INTENT", "on").strip().lower() not in ("", "0", "false", "off")
# 明确短句（纯寒暄 / 品类+购买词）直接用本地规则判定意图，跳过意图 LLM。
INTENT_RULE_SHORTCUT = os.getenv("MALL_AGENT_INTENT_RULE_SHORTCUT", "on").strip().lower() not in ("", "0", "false", "off")
# 单次 LLM 的读/连接超时与重试次数：避免一个卡死调用占住 worker 十分钟。
LLM_TIMEOUT_SECONDS = float(os.getenv("LLM_TIMEOUT_SECONDS", "60"))
LLM_CONNECT_TIMEOUT_SECONDS = float(os.getenv("LLM_CONNECT_TIMEOUT_SECONDS", "10"))
LLM_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "1"))

# Conversation context source. When on (default), the worker rebuilds the LLM /
# RAG context from MySQL history by conversation_id (authoritative) instead of
# trusting the client's self-reported full message list; when off, the legacy
# client-fed path is kept (rollback). history_service.get_message_context
# supplies the window; see worker.py and service.stream_messages.
CTX_SERVER = os.getenv("MALL_AGENT_CTX_SERVER", "on").strip().lower() not in ("", "0", "false", "off")
# Max messages fed to the LLM as conversation context (server mode only; the
# legacy client path keeps its historical 8-message window).
CTX_MAX_MESSAGES = int(os.getenv("MALL_AGENT_CTX_MAX_MESSAGES", "20"))
# How many recent prior user turns are joined ahead of the current query when
# calling the RAG /query, so cross-turn pronouns carry referent context.
CTX_RAG_HISTORY = int(os.getenv("MALL_AGENT_CTX_RAG_HISTORY", "3"))

# RAG knowledge base (external HTTP service, see RAG-MCP-SERVER http_server)
RAG_HTTP_URL = os.getenv("RAG_HTTP_URL", "")  # empty = disabled
RAG_TOP_K = int(os.getenv("RAG_TOP_K", "3"))
RAG_TIMEOUT_SECONDS = float(os.getenv("RAG_TIMEOUT_SECONDS", "1.5"))
RAG_SNIPPET_MAX_CHARS = int(os.getenv("RAG_SNIPPET_MAX_CHARS", "500"))

# RAG runtime master switch. Unlike RAG_HTTP_URL (empty = disabled), this is
# an explicit off for process start — the runtime toggle itself lives on
# knowledge_client.set_enabled(). "off"|"0"|"false"|"" (any case) disables.
RAG_ENABLED = os.getenv("MALL_AGENT_RAG", "on").strip().lower() not in ("", "0", "false", "off")

# DeepSeek cost constants (CNY per million tokens, override via env)
DS_PRICE_INPUT_RMB_PER_M = float(os.getenv("DS_PRICE_INPUT_RMB_PER_M", "0.27"))
DS_PRICE_OUTPUT_RMB_PER_M = float(os.getenv("DS_PRICE_OUTPUT_RMB_PER_M", "1.1"))

# ── P0-02：浏览器跨域白名单 ─────────────────────────────────────────────
# 逗号分隔的完整 Origin（含 scheme+host+port），不支持通配符。默认只放本地两个
# 前端端口；生产经环境变量显式配置，同源部署时设空串（不输出任何 CORS 头）。
# agent 用 Authorization 头而非 cookie，因此 run_mall.py 里 allow_credentials=False。
CORS_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "CORS_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173,"
        "http://localhost:5174,http://127.0.0.1:5174",
    ).split(",")
    if origin.strip()
]

# ── #106：portal JWT 验签（防 user_id 伪装）────────────────────────────
# mall-agent 与 mall-portal 是受信内网两方：agent 收到带 `Authorization: Bearer <portal 会员 JWT>`
# 的请求时验签并提取 memberId claim，与请求自报的 user_id 比对（不一致拒绝，防跨用户会话伪装）。
# - PORTAL_JWT_SECRET 留空 = 验签关闭（默认）：无 header 与带任意 header 的请求均放行（内网 demo 边界，
#   见 PROD-HIGH-CONCURRENCY-DESIGN §5.4 诚实边界）。生产须配置与 mall-portal application.yml `jwt.secret`
#   一致的值（敏感值只经环境/.env，不入 git）。
# - PORTAL_ADMIN_USERS：admin 端固定会话用户（mall-admin-web 用常量 'admin'，走 admin JWT、其 secret 与
#   portal 不同无法交叉验签）→ 对该用户名请求跳过比对（双通道，见 r106-supply §3.3）。
PORTAL_JWT_SECRET = os.getenv("PORTAL_JWT_SECRET", "")
PORTAL_JWT_ALGORITHM = os.getenv("PORTAL_JWT_ALGORITHM", "HS256")  # hutool JWTUtil.createToken 默认 HS256
PORTAL_ADMIN_USERS = {u for u in os.getenv("PORTAL_ADMIN_USERS", "admin").split(",") if u.strip()}

# ── P0-01：管理面（/api/monitor/*、/queue/status、SSE 订阅）鉴权 ────────
# 默认 on（fail-closed）：未配置任何凭据时管理面一律 401，而不是裸奔。
# 三条通道（任一通过）：
#   1. ``X-Monitor-Token`` == ``MONITOR_TOKEN``（运维脚本 / curl；空 = 关闭该通道）；
#   2. ``Authorization: Bearer <admin JWT>``：用 ``ADMIN_JWT_SECRET``（= mall-admin
#      application.yml 的 jwt.secret）验签，且 sub/username 在 ``MONITOR_ADMIN_USERS`` 内；
#   3. ``Authorization: Bearer <portal 会员 JWT>``：用 ``PORTAL_JWT_SECRET`` 验签，且
#      memberId 在 ``PORTAL_ADMIN_USERS`` 内（兼容既有 admin 双通道）。
# 回滚（仅限本地排障）：MONITOR_AUTH=off → 管理面恢复匿名。
MONITOR_AUTH = os.getenv("MONITOR_AUTH", "on").strip().lower() not in ("", "0", "false", "off")
MONITOR_TOKEN = os.getenv("MONITOR_TOKEN", "")
# admin 登录 token 的验签密钥；与 mall-admin 的 jwt.secret 保持一致。
# 空 = 不接受 admin JWT 通道（admin 监控页需改用 X-Monitor-Token 或配置此项）。
ADMIN_JWT_SECRET = os.getenv("ADMIN_JWT_SECRET", "")
MONITOR_ADMIN_USERS = {u for u in os.getenv("MONITOR_ADMIN_USERS", "admin").split(",") if u.strip()}
