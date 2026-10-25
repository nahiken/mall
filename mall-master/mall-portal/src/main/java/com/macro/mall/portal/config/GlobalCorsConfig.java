package com.macro.mall.portal.config;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.cors.CorsConfiguration;
import org.springframework.web.cors.UrlBasedCorsConfigurationSource;
import org.springframework.web.filter.CorsFilter;

import java.util.Arrays;

/**
 * 全局跨域相关配置
 * Created by macro on 2019/7/27.
 *
 * P0-02：只允许 `cors.allowed-origins`（环境变量 CORS_ALLOWED_ORIGINS，
 * 逗号分隔）里的来源；dev 默认本地前端端口，prod 由 application-prod.yml
 * 强制显式注入（不注入则启动失败）。
 */
@Configuration
public class GlobalCorsConfig {

    private final String[] allowedOrigins;

    public GlobalCorsConfig(@Value("${cors.allowed-origins}") String allowedOrigins) {
        this.allowedOrigins = Arrays.stream(allowedOrigins.split(","))
                .map(String::trim)
                .filter(origin -> !origin.isEmpty())
                .toArray(String[]::new);
    }

    /**
     * 允许跨域调用的过滤器（仅白名单 Origin）
     */
    @Bean
    public CorsFilter corsFilter() {
        CorsConfiguration config = new CorsConfiguration();
        for (String origin : allowedOrigins) {
            config.addAllowedOrigin(origin);
        }
        //允许跨域发送cookie（JWT 走 Authorization 头，保留以兼容旧前端）
        config.setAllowCredentials(true);
        //放行全部原始头信息
        config.addAllowedHeader("*");
        //允许所有请求方法跨域调用
        config.addAllowedMethod("*");
        UrlBasedCorsConfigurationSource source = new UrlBasedCorsConfigurationSource();
        source.registerCorsConfiguration("/**", config);
        return new CorsFilter(source);
    }
}
