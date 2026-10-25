-- =====================================================================
-- #107 阶段2 · 商城生产化一致性 DDL（索引 + 唯一约束）
-- 仓库: D:\复合\work\rag\mall · 分支: codex/agent · 日期: 2026-09-05
-- 目标库: mall（与 document/sql/mall.sql 同库）
--
-- 说明（重要）:
--   1. MySQL 不支持 CREATE INDEX IF NOT EXISTS；本脚本设计为【一次性执行】。
--      重复执行会报 "Duplicate key name"——属预期无害结果，跳过即可。
--      执行后用文末 SHOW INDEX 验证即可确认。
--   2. 本脚本不随服务启动自动执行（用户决定：写脚本、不自动执行）。
--      由运维/开发在维护窗口手动执行一次。
--   3. 各语句的"为什么"见右侧注释；涉及代码逻辑见 mall-portal
--      OmsPortalOrderServiceImpl / PortalOrderDao.xml（同 commit）。
-- =====================================================================

USE mall;

-- ---------------------------------------------------------------------
-- 1. pms_product 读过滤复合索引
--    portal 所有 read（search/home 列表）公共过滤 = delete_status=0 AND publish_status=1
--    → 复合前缀索引让等值过滤走索引，避免每次全表扫。
--    name LIKE '%kw%' 前导通配用不上 BTREE，故不加 name 前缀索引（诚实标注）。
-- ---------------------------------------------------------------------
ALTER TABLE pms_product ADD INDEX idx_pms_product_status (delete_status, publish_status);

-- search 按品牌等值筛选
ALTER TABLE pms_product ADD INDEX idx_pms_product_brand (brand_id);

-- search 按分类等值筛选
ALTER TABLE pms_product ADD INDEX idx_pms_product_category (product_category_id);

-- 商品列表默认按 id desc（新品），销量/价格排序可选；给排序列兜底
ALTER TABLE pms_product ADD INDEX idx_pms_product_sort_sale (sale);
ALTER TABLE pms_product ADD INDEX idx_pms_product_sort_price (price);

-- ---------------------------------------------------------------------
-- 2. pms_sku_stock 按 product_id 查询索引
--    detail/cart/热销都按 product_id 拉 SKU 列表，原表无此二级索引。
-- ---------------------------------------------------------------------
ALTER TABLE pms_sku_stock ADD INDEX idx_pms_sku_stock_product (product_id);

-- ---------------------------------------------------------------------
-- 3. oms_order.order_sn 唯一索引（兜底 #107 订单号防重）
--    背景: order_sn 由 Redis INCR(mall:oms:orderId{yyyyMMdd}) 生成，
--    已补 key TTL；此唯一索引作为 DB 层最终兜底，杜绝 order_sn 重复
--    导致 paySuccessByOrderSn 查错单。mall.sql 种子 65 行 order_sn 已全局唯一，
--    可安全添加。
-- ---------------------------------------------------------------------
ALTER TABLE oms_order ADD UNIQUE INDEX uk_oms_order_sn (order_sn);

-- ---------------------------------------------------------------------
-- 4. oms_order_item.order_id 查询索引
--    getDetail / order list 均按 order_id JOIN 明细，原表无二级索引。
-- ---------------------------------------------------------------------
ALTER TABLE oms_order_item ADD INDEX idx_oms_order_item_order (order_id);

-- ---------------------------------------------------------------------
-- 5. oms_order 用户订单列表索引（可选，写少读多场景提升明显）
--    list 查询: WHERE member_id=? AND delete_status=0 [AND status=?] ORDER BY create_time DESC
-- ---------------------------------------------------------------------
ALTER TABLE oms_order ADD INDEX idx_oms_order_member_list (member_id, delete_status, status, create_time);

-- =====================================================================
-- 验证（执行后手动跑）
-- =====================================================================
-- SHOW INDEX FROM pms_product WHERE Key_name IN ('idx_pms_product_status','idx_pms_product_brand','idx_pms_product_category');
-- SHOW INDEX FROM pms_sku_stock WHERE Key_name = 'idx_pms_sku_stock_product';
-- SHOW INDEX FROM oms_order WHERE Key_name IN ('uk_oms_order_sn','idx_oms_order_member_list');
-- SHOW INDEX FROM oms_order_item WHERE Key_name = 'idx_oms_order_item_order';
