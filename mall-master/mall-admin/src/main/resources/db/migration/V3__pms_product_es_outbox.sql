-- =====================================================================
-- P2-03 · 商品 → ES 增量同步的 outbox 表
--
-- 为什么是 outbox 而不是直接调 ES：
--   mall-admin 是商品写入方，mall-search 才持有 ES 客户端。若在 admin 里同步调
--   search 的写接口，一次 ES 抖动/超时就会把商品保存/上下架一起拖失败——这违反
--   "ES 故障不影响主流程"。outbox 把"要同步"这个意图和"同步成功"解耦：
--     1) admin 在**同一个事务**里写商品 + 写一行 outbox（op=upsert/delete）；
--     2) mall-search 的调度器轮询 pending 行，调 ES 成功后标记 done；
--     3) 失败按指数退避重试，超过上限进 dead（status=2），不阻塞业务写入。
--
-- 状态机：0 pending → (成功) 1 done / (超过 max_attempts) 2 dead
--   dead 的行保留 last_error，供人工排查后重置为 0 重新消费。
--
-- 索引说明：
--   idx_es_outbox_claim (status, next_attempt_at, id) —— 消费者按"到点的 pending
--   行、id 升序"批量取，走覆盖前缀；
--   idx_es_outbox_product (product_id, status) —— 写侧去重（同一商品连续改 3 次，
--   消费者只要最终态）与排障"这个商品为什么没同步"。
--
-- 幂等：CREATE TABLE IF NOT EXISTS，重复执行安全（与 V2 的幂等原则一致）。
-- =====================================================================

CREATE TABLE IF NOT EXISTS `pms_product_es_outbox` (
  `id`              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '自增主键，同时作为消费顺序',
  `product_id`      BIGINT          NOT NULL                COMMENT 'pms_product.id',
  `op`              VARCHAR(16)     NOT NULL                COMMENT 'upsert=新增/编辑/上架，delete=删除/下架',
  `status`          TINYINT         NOT NULL DEFAULT 0      COMMENT '0=pending 1=done 2=dead',
  `attempts`        INT             NOT NULL DEFAULT 0      COMMENT '已尝试次数（指数退避用）',
  `last_error`      VARCHAR(500)             DEFAULT NULL   COMMENT '最近一次失败原因（截断）',
  `next_attempt_at` DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '下次可被领取的时间',
  `created_at`      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at`      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_es_outbox_claim` (`status`, `next_attempt_at`, `id`),
  KEY `idx_es_outbox_product` (`product_id`, `status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='P2-03 商品 ES 同步 outbox';
