# #108 前端产物镜像（多阶段：node 构建 → nginx 托管静态 + 反代 gateway）。
# 一次构建内置 admin + app 两个 dist；nginx.conf 把 admin serve 在 /、app serve 在 /app/。
# 构建上下文：仓库根（两个前端 dist 均需）。示例：
#   docker build -f deploy/nginx/Dockerfile -t mall-web:1.0-SNAPSHOT .
# dist 须先本地构建：app 用 `npm run build:h5 -- --mode production`（uni 默认读 .env.production，
#   产物在 dist/build/h5），admin 用 `npm run build-only`（读 .env.production，产物在 dist）。
FROM node:20-alpine AS web-admin
WORKDIR /src
COPY mall-admin-web-master/package.json mall-admin-web-master/package-lock.json* ./
RUN npm ci || npm install
COPY mall-admin-web-master/ .
RUN npm run build-only

FROM node:20-alpine AS web-app
WORKDIR /src
COPY mall-app-web-master/package.json mall-app-web-master/package-lock.json* ./
RUN npm ci || npm install
COPY mall-app-web-master/ .
RUN npm run build:h5

FROM nginx:1.27-alpine
COPY deploy/nginx/nginx.conf /etc/nginx/nginx.conf
# admin dist → /usr/share/nginx/html/（location / root）；app h5 dist → /usr/share/nginx/html/app/
COPY --from=web-admin /src/dist/ /usr/share/nginx/html/
COPY --from=web-app /src/dist/build/h5/ /usr/share/nginx/html/app/
EXPOSE 80
# P2-06：健康检查打 nginx 自己的 /healthz（不反代上游），只反映静态服务与配置可用。
# 用 busybox wget：alpine 自带，不额外装包。
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD wget -q --spider http://127.0.0.1/healthz || exit 1
