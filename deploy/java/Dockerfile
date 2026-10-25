# #108 Java 各服务镜像（手写多阶段，本机 docker build 走本地 daemon；
# 生产也可沿用 mall-master document/shard 或 fabric8 方案 —— 两套并存互不冲突）
# 构建上下文：mall-master/ 目录（相对本文件向上两级）。服务由 ARG 指定，一次构建一个：
#   docker build -f deploy/java/Dockerfile -t mall-admin:1.0-SNAPSHOT \
#       --build-arg MODULE=mall-admin --build-arg PORT=8080 mall-master/
#   portal PORT=8085 / search PORT=8081 / gateway PORT=8090

# ---- 构建阶段：本地 maven 仓库离线优先，在线兜底 ----
FROM maven:3.9-eclipse-temurin-17 AS builder
ARG MODULE
WORKDIR /build
COPY pom.xml ./
COPY ${MODULE}/pom.xml ${MODULE}/pom.xml
# 父模块依赖（security/mbg/common 等按需）；此处用 mvn -pl 离线编译会缺兄弟模块，
# 故直接全量 package（跳测试），产物 jar 从 target 取
COPY . .
RUN mvn -q -DskipTests package

# ---- 运行阶段 ----
FROM eclipse-temurin:17-jre
ARG MODULE
ARG PORT=8080
# P2-06：compose 的 healthcheck 需要知道端口；ARG 只在构建期可见，转成 ENV
ENV APP_PORT=${PORT}
WORKDIR /app
COPY --from=builder /build/${MODULE}/target/*.jar /app/app.jar
# P2-06：非 root 运行（固定 uid 10001）。/app 归 app 所有，方便将来挂载只读配置
RUN groupadd -g 10001 app \
    && useradd -u 10001 -g app -m -s /usr/sbin/nologin app \
    && chown -R app:app /app
USER 10001
EXPOSE ${PORT}
# #108：secret 经环境变量注入（application-prod.yml 已改 ${ENV} 占位，无默认即启动失败逼注入）
ENV SPRING_PROFILES_ACTIVE=prod
# P2-06：存活检查用 TCP 连接而不是 /actuator/health —— 本仓 Java 模块目前**没有**
# 引入 spring-boot-starter-actuator，且 P0-03 已把 /actuator/** 移出匿名白名单，
# 配一个打不通的 URL 只会让 compose 永远 unhealthy。TCP 检查能反映"进程崩了/
# 端口没起"（最常见故障），读不到业务就绪度——这一条已记入 §13.3 诚实边界。
HEALTHCHECK --interval=30s --timeout=3s --start-period=90s --retries=3 \
    CMD bash -c 'exec 3<>/dev/tcp/127.0.0.1/'"${APP_PORT}"
ENTRYPOINT ["java", "-jar", "/app/app.jar"]
