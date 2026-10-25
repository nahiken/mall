package com.macro.mall.gateway.config;

import org.springframework.cloud.gateway.filter.GatewayFilterChain;
import org.springframework.cloud.gateway.filter.GlobalFilter;
import org.springframework.core.Ordered;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Component;
import org.springframework.web.server.ServerWebExchange;
import reactor.core.publisher.Mono;

import java.util.regex.Pattern;

/**
 * 搜索写接口网关守卫（P0-05）。
 *
 * <p>mall-search 未引 Spring Security，{@code /esProduct/importAll}、{@code /delete/**}、
 * {@code /create/**} 原先对公网裸放——任何人都能清空/重建 ES 索引。本过滤器在网关统一入口
 * 拦截这些路径并返回 403，无论 HTTP 方法（旧的 {@code GET /delete/{id}} 一并拦住）。
 *
 * <p>为什么不在网关校验内部 token：网关是公网边缘，token 一旦下发就等于公开；内部运维
 * 应直连 {@code :8081} 并携带 {@code X-Internal-Token}（控制器二次校验，见
 * {@code EsProductController#requireInternalToken}）。因此这里是「网关拒绝 + 控制器内部
 * token」双保险，而不是把 token 逻辑搬到边缘。
 *
 * <p>读接口（{@code /search}、{@code /search/simple}、{@code /recommend/{id}}、
 * {@code /search/relate}）不在拦截范围内。
 *
 * <p>回滚：删除本类或调整 {@link #WRITE_PATH} 即可恢复旧行为（需重新构建网关）。
 */
@Component
public class SearchWriteGuardFilter implements GlobalFilter, Ordered {

    /** 写接口路径（网关视角仍带 /search-api 前缀，RewritePath 之后才是上游路径）。 */
    private static final Pattern WRITE_PATH = Pattern.compile(
            "^/search-api/esProduct/(importAll|create(?:/.*)?|delete(?:/.*)?)/?$");

    @Override
    public Mono<Void> filter(ServerWebExchange exchange, GatewayFilterChain chain) {
        String path = exchange.getRequest().getURI().getPath();
        if (WRITE_PATH.matcher(path).matches()) {
            exchange.getResponse().setStatusCode(HttpStatus.FORBIDDEN);
            return exchange.getResponse().setComplete();
        }
        return chain.filter(exchange);
    }

    @Override
    public int getOrder() {
        // 尽早在路由/限流之前拦截，避免为恶意写请求消耗 Redis 令牌。
        return Ordered.HIGHEST_PRECEDENCE;
    }
}
