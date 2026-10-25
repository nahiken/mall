package com.macro.mall.gateway.config;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.cloud.gateway.filter.ratelimit.KeyResolver;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import reactor.core.publisher.Mono;

/**
 * 网关限流 KeyResolver（#106）。
 *
 * <p>限流维度：默认按<b>客户端 IP</b>（真实 TCP 对端地址，见下）。IP 维度对匿名读热点与
 * search 裸写接口都适用——网关是统一入口；本模块不另做鉴权（业务鉴权在各上游，见
 * {@code MallGatewayApplication}）。
 *
 * <p>IP 键设计：本网关默认是<b>边缘入口</b>（当前直接暴露、前端无受信反向代理/LB），因此直接取
 * {@code request.getRemoteAddress()}（连接的真实对端 IP）。X-Forwarded-For 在此拓扑下
 * 客户端可自造，不可作为键。若把网关置于 nginx/LB 之后（#108 容器化编排：nginx :80 → gateway
 * :8090），则 remoteAddress 恒为 nginx 容器 IP，IP 维度会塌缩成单桶——此时须设环境变量
 * {@code RATE_LIMIT_TRUST_PROXY=true}，改读 nginx 透传的 {@code X-Real-IP}（nginx 以
 * {@code proxy_set_header X-Real-IP $remote_addr} 覆盖客户端自造值，故可信）。若要按登录用户
 * 维度限流，可在网关校验 token 后改按 user/memberId 分桶（当前阶段不解析业务 token，避免与上游
 * 鉴权重叠）。
 *
 * <p>Created by macro on 2026/9/5.
 */
@Configuration
public class RateLimiterKeyResolverConfig {

    /** Redis RequestRateLimiter 默认 KeyResolver Bean 名（spring.cloud.gateway.filter.request-rate-limiter.key-resolver） */
    public static final String IP_KEY_RESOLVER = "ipKeyResolver";

    /** #108：受信反代开关。默认 false（网关直连，remoteAddress=真实客户端 IP）；
     *  true（网关置于 nginx/LB 之后）时读 nginx 覆盖写的 X-Real-IP，避免 IP 桶塌缩成单桶。 */
    @Value("${RATE_LIMIT_TRUST_PROXY:false}")
    private boolean trustProxy;

    @Bean(IP_KEY_RESOLVER)
    public KeyResolver ipKeyResolver() {
        return exchange -> {
            if (trustProxy) {
                String realIp = exchange.getRequest().getHeaders().getFirst("X-Real-IP");
                if (realIp != null && !realIp.isBlank()) {
                    return Mono.just(realIp);
                }
            }
            return Mono.just(exchange.getRequest().getRemoteAddress() != null
                    ? exchange.getRequest().getRemoteAddress().getAddress().getHostAddress()
                    : "unknown");
        };
    }
}
