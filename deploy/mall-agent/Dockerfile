# #108 mall-agent 容器镜像（Python FastAPI 服务，单 worker 多协程）
# 构建上下文：仓库根。不携带 .env —— 配置全走环境变量注入（见 deploy/docker-compose.yml）。
FROM python:3.11-slim

# 时区：仅 TZ 数据，非 tzdata 包；日志用系统时区（默认 UTC，应用打印均秒级时间戳无强依赖）
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# 先装依赖层（利用层缓存；requirements 变动才重装）
COPY deepseek_agent/llm_backend/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# 应用源码（根 .dockerignore 已排除 **/.env、.venv、__pycache__、logs）
COPY deepseek_agent/llm_backend /app

# P2-06：健康检查脚本（独立文件，本机可直接执行验证，见 deploy/mall-agent/healthcheck.py）
COPY deploy/mall-agent/healthcheck.py /app/healthcheck.py

# P2-06：非 root 运行。uid/gid 固定 10001，便于宿主挂载卷时对齐权限；
# /app/logs 是 stats 写 metrics.jsonl 的位置（P1-06），必须对 app 用户可写。
RUN groupadd -g 10001 app \
    && useradd -u 10001 -g app -m -s /usr/sbin/nologin app \
    && mkdir -p /app/logs \
    && chown -R app:app /app
USER 10001

EXPOSE 8002

# P2-06：健康检查走应用自己的 /health（run_mall.py），不是端口探测——
# 端口能连上不代表事件循环还能响应（DB 池耗尽时正是这种半死状态）。
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python /app/healthcheck.py

# config.py 默认 PORT=8001；compose 注入 PORT=8002。HOST 默认 0.0.0.0。
# SIGTERM 由 uvicorn 交给 lifespan shutdown：先 aclose stats writer（P1-06）再退。
STOPSIGNAL SIGTERM
CMD ["python", "run_mall.py"]
