-- =====================================================================
-- P2-02 · Flyway V2：生产一致性索引（幂等）
--
-- 来源：mall-master/document/sql/mall_prod_consistency.sql（#107 阶段2）
-- 与原脚本的唯一差别：**幂等化**。
--
-- 为什么必须幂等：
--   MySQL 8 没有 `CREATE INDEX IF NOT EXISTS`，原脚本设计为"手动执行一次"。
--   一旦交给 Flyway，它会出现在三种库里：
--     1. 空库（V1 刚建完）→ 需要建索引；
--     2. 已有库，运维已手动跑过 consistency 脚本 → 索引都在，再跑会
--        "Duplicate key name" 直接把迁移打红；
--     3. 已有库，只跑过一部分 → 需要补齐缺的那几条。
--   下面的写法对三种情况都成立：先查 information_schema.statistics，
--   缺了才 ALTER，已存在就 `DO 0`（合法空语句）。
--
-- 不改的：索引名、列顺序、唯一性都与原脚本一致，便于用
-- `SHOW INDEX` 对照原脚本的验证段。
-- =====================================================================

-- ---------------------------------------------------------------------
-- 1. pms_product：读过滤 / 品牌 / 分类 / 销量 / 价格
--    portal 所有列表 read 的公共过滤 = delete_status=0 AND publish_status=1
-- ---------------------------------------------------------------------
SET @idx_exists := (SELECT COUNT(*) FROM information_schema.statistics
  WHERE table_schema = DATABASE() AND table_name = 'pms_product'
    AND index_name = 'idx_pms_product_status');
SET @ddl := IF(@idx_exists = 0,
  'ALTER TABLE `pms_product` ADD INDEX `idx_pms_product_status` (`delete_status`, `publish_status`)',
  'DO 0');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @idx_exists := (SELECT COUNT(*) FROM information_schema.statistics
  WHERE table_schema = DATABASE() AND table_name = 'pms_product'
    AND index_name = 'idx_pms_product_brand');
SET @ddl := IF(@idx_exists = 0,
  'ALTER TABLE `pms_product` ADD INDEX `idx_pms_product_brand` (`brand_id`)',
  'DO 0');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @idx_exists := (SELECT COUNT(*) FROM information_schema.statistics
  WHERE table_schema = DATABASE() AND table_name = 'pms_product'
    AND index_name = 'idx_pms_product_category');
SET @ddl := IF(@idx_exists = 0,
  'ALTER TABLE `pms_product` ADD INDEX `idx_pms_product_category` (`product_category_id`)',
  'DO 0');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @idx_exists := (SELECT COUNT(*) FROM information_schema.statistics
  WHERE table_schema = DATABASE() AND table_name = 'pms_product'
    AND index_name = 'idx_pms_product_sort_sale');
SET @ddl := IF(@idx_exists = 0,
  'ALTER TABLE `pms_product` ADD INDEX `idx_pms_product_sort_sale` (`sale`)',
  'DO 0');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @idx_exists := (SELECT COUNT(*) FROM information_schema.statistics
  WHERE table_schema = DATABASE() AND table_name = 'pms_product'
    AND index_name = 'idx_pms_product_sort_price');
SET @ddl := IF(@idx_exists = 0,
  'ALTER TABLE `pms_product` ADD INDEX `idx_pms_product_sort_price` (`price`)',
  'DO 0');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- ---------------------------------------------------------------------
-- 2. pms_sku_stock：detail / cart / 热销都按 product_id 拉 SKU
-- ---------------------------------------------------------------------
SET @idx_exists := (SELECT COUNT(*) FROM information_schema.statistics
  WHERE table_schema = DATABASE() AND table_name = 'pms_sku_stock'
    AND index_name = 'idx_pms_sku_stock_product');
SET @ddl := IF(@idx_exists = 0,
  'ALTER TABLE `pms_sku_stock` ADD INDEX `idx_pms_sku_stock_product` (`product_id`)',
  'DO 0');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- ---------------------------------------------------------------------
-- 3. oms_order：order_sn 唯一索引（兜底 Redis INCR 生成的订单号重复）
--    种子数据 65 行 order_sn 已全局唯一，可安全添加。
-- ---------------------------------------------------------------------
SET @idx_exists := (SELECT COUNT(*) FROM information_schema.statistics
  WHERE table_schema = DATABASE() AND table_name = 'oms_order'
    AND index_name = 'uk_oms_order_sn');
SET @ddl := IF(@idx_exists = 0,
  'ALTER TABLE `oms_order` ADD UNIQUE INDEX `uk_oms_order_sn` (`order_sn`)',
  'DO 0');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- ---------------------------------------------------------------------
-- 4. oms_order_item：getDetail / 订单列表按 order_id JOIN 明细
-- ---------------------------------------------------------------------
SET @idx_exists := (SELECT COUNT(*) FROM information_schema.statistics
  WHERE table_schema = DATABASE() AND table_name = 'oms_order_item'
    AND index_name = 'idx_oms_order_item_order');
SET @ddl := IF(@idx_exists = 0,
  'ALTER TABLE `oms_order_item` ADD INDEX `idx_oms_order_item_order` (`order_id`)',
  'DO 0');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- ---------------------------------------------------------------------
-- 5. oms_order：会员订单列表
--    WHERE member_id=? AND delete_status=0 [AND status=?] ORDER BY create_time DESC
-- ---------------------------------------------------------------------
SET @idx_exists := (SELECT COUNT(*) FROM information_schema.statistics
  WHERE table_schema = DATABASE() AND table_name = 'oms_order'
    AND index_name = 'idx_oms_order_member_list');
SET @ddl := IF(@idx_exists = 0,
  'ALTER TABLE `oms_order` ADD INDEX `idx_oms_order_member_list` (`member_id`, `delete_status`, `status`, `create_time`)',
  'DO 0');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;
