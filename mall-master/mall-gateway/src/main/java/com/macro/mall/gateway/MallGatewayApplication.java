package com.macro.mall.gateway;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

/**
 * 统一入口网关（#106）。
 *
 * <p>职责边界：本模块是纯<b>转发层</b>——限流 + 反代聚合（/agent-api→agent、/mall-api→portal、
 * /search-api→mall-search）。业务鉴权由各上游自行承担（portal/admin 内部已有 JWT 机制；
 * agent 的 user_id 伪装在 mall-agent 侧验签比对，见 deepseek_agent auth.py）。
 *
 * <p>不依赖 mall-security / mall-mbg：网关只透传请求头，不解析业务 token，避免引入
 * Spring MVC / 数据源等与 WebFlux 转发层冲突的依赖。
 *
 * Created by macro on 2026/9/5.
 */
@SpringBootApplication
public class MallGatewayApplication {

    public static void main(String[] args) {
        SpringApplication.run(MallGatewayApplication.class, args);
    }
}
