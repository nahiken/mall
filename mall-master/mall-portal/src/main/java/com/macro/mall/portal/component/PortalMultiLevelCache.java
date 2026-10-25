package com.macro.mall.portal.component;

import com.github.benmanes.caffeine.cache.Cache;
import com.github.benmanes.caffeine.cache.Caffeine;
import com.macro.mall.common.service.RedisService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Component;

import java.time.Duration;
import java.util.concurrent.ConcurrentHashMap;
import java.util.function.Supplier;

/**
 * 前台多级缓存组件（手写，不引 spring-cache）：Caffeine(L1) → Redis(L2) → DB(loader)。
 * #107 为"写少读多的固定形态读"（分类树/首页列表 page1）而加，仅 portal 内部使用。
 *
 * <p>层级职责：
 * <ul>
 *   <li><b>L1 Caffeine</b>：进程内热层，拦截本实例热点，固定短 TTL（30s），非一致性权威。</li>
 *   <li><b>L2 Redis</b>：跨实例共享真值层，TTL 由调用方按数据容忍度指定（一致性收敛上界）。</li>
 *   <li><b>DB loader</b>：双 miss 回源；Redis 异常时降级直查（读可用性优先，不因缓存故障打断读）。</li>
 * </ul>
 *
 * <p>失效策略：本缓存面向管理端<b>低频变更</b>数据（分类/首页推荐配置），用双级 TTL 自然收敛，
 * 不接 admin 变更广播（详见 docs/reviews/r107-supply.md §D3）。
 *
 * <p>并发：缓存内对象视为<b>只读</b>返回给调用方（序列化给前端），调用方不得修改。
 *
 * Created by macro on 2026/9/5.
 */
@Component
public class PortalMultiLevelCache {

    private static final Logger LOGGER = LoggerFactory.getLogger(PortalMultiLevelCache.class);

    /** Redis key 前缀：与既有 mall:ums:member:{x} 等惯例一致，portal 缓存单独命名空间防碰撞 */
    private static final String CACHE_KEY_PREFIX = "mall:portal:cache";
    /** L1 本地缓存统一短 TTL：仅为拦截本机热点，非一致性窗口 */
    private static final long L1_TTL_SECONDS = 30;
    /** 每个命名区 L1 最大条目数（超限走 LRU 淘汰） */
    private static final long L1_MAX_SIZE = 200;

    @Autowired
    private RedisService redisService;

    /** cacheName → Caffeine 本地缓存（每个命名区独立构建） */
    private final ConcurrentHashMap<String, Cache<String, Object>> localCaches = new ConcurrentHashMap<>();

    /**
     * 三级取数：L1 → L2 → loader。命中即返回（不跨级回写，值已完整）。空结果(null)不缓存，防穿透。
     *
     * @param cacheName        缓存命名区，如 "categoryTree" / "homeHot"
     * @param key              区内的业务键，如分类树恒为 "all"、首页列表为 "pageSize:6"
     * @param redisTtlSeconds  L2 Redis 有效期（秒），决定 admin 变更后全端收敛上界
     * @param loader           DB 回源函数
     * @param <V>              缓存值类型（POJO/List，须可被既有 redisTemplate JSON 序列化）
     */
    @SuppressWarnings("unchecked")
    public <V> V get(String cacheName, String key, long redisTtlSeconds, Supplier<V> loader) {
        String localKey = cacheName + ":" + key;
        // L1 本地缓存。L1 TTL 取 min(固定 30s, L2 TTL)，保证短时效数据（如首页秒杀 10s）
        // 不会在 L1 里多活到 30s；同一 cacheName 首次创建时定档，调用方需保持 TTL 一致。
        long l1Ttl = Math.min(L1_TTL_SECONDS, redisTtlSeconds);
        Cache<String, Object> l1 = localCaches.computeIfAbsent(cacheName,
                name -> buildLocalCache(l1Ttl));
        Object cached = l1.getIfPresent(localKey);
        if (cached != null) {
            return (V) cached;
        }
        // L2 Redis
        String redisKey = buildRedisKey(cacheName, key);
        try {
            Object l2Value = redisService.get(redisKey);
            if (l2Value != null) {
                // 反填 L1，缩短后续本机访问路径
                l1.put(localKey, l2Value);
                return (V) l2Value;
            }
        } catch (Exception e) {
            // Redis 异常：降级直查 DB，读可用性优先
            LOGGER.warn("PortalMultiLevelCache redis read failed, degrade to DB. cache={} key={} err={}",
                    cacheName, key, e.getMessage());
        }
        // DB 回源
        V loaded = loader.get();
        if (loaded == null) {
            return null; // 空结果不缓存，防穿透
        }
        l1.put(localKey, loaded);
        try {
            redisService.set(redisKey, loaded, redisTtlSeconds);
        } catch (Exception e) {
            LOGGER.warn("PortalMultiLevelCache redis write failed, skip L2. cache={} key={} err={}",
                    cacheName, key, e.getMessage());
        }
        return loaded;
    }

    /**
     * 主动失效单个键（L1 + L2）。用于「缓存里的值类型/结构不可用」等异常自愈：
     * 调用方 catch 到反序列化/类型异常后清掉脏值，下次访问重新回源。
     */
    public void evict(String cacheName, String key) {
        Cache<String, Object> l1 = localCaches.get(cacheName);
        if (l1 != null) {
            l1.invalidate(cacheName + ":" + key);
        }
        try {
            redisService.del(buildRedisKey(cacheName, key));
        } catch (Exception e) {
            LOGGER.warn("PortalMultiLevelCache redis evict failed. cache={} key={} err={}",
                    cacheName, key, e.getMessage());
        }
    }

    private Cache<String, Object> buildLocalCache(long l1TtlSeconds) {
        return Caffeine.newBuilder()
                .expireAfterWrite(Duration.ofSeconds(l1TtlSeconds))
                .maximumSize(L1_MAX_SIZE)
                .build();
    }

    private String buildRedisKey(String cacheName, String key) {
        return CACHE_KEY_PREFIX + ":" + cacheName + ":" + key;
    }
}
