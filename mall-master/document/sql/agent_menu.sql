-- 阶段13：管理后台新增 "AI 助手" 一级菜单（Agent 会话 / 实时监控 / 压测指标）
-- 执行方式：在 mall 数据库执行（如 mysql -uroot -p mall < agent_menu.sql）
-- 说明：ums_menu.name 必须与前端路由 name 对齐（agent / agentChat / agentMonitor / agentLoadtest），
--      否则 permission.ts 的 hasPermission 会把路由过滤掉、侧边栏不显示。

-- 1. 一级菜单 "AI 助手"（parentId=0）
INSERT INTO `ums_menu` VALUES (26, 0, NOW(), 'AI 助手', 0, 90, 'agent', 'agent', 0);

-- 2. 三个子菜单
INSERT INTO `ums_menu` VALUES (27, 26, NOW(), 'Agent 会话', 1, 90, 'agentChat', 'agent-chat', 0);
INSERT INTO `ums_menu` VALUES (28, 26, NOW(), '实时监控', 1, 91, 'agentMonitor', 'agent-monitor', 0);
INSERT INTO `ums_menu` VALUES (29, 26, NOW(), '压测指标', 1, 92, 'agentLoadtest', 'agent-loadtest', 0);

-- 3. 给"超级管理员"角色（ums_role id=5）分配这四个菜单
INSERT INTO `ums_role_menu_relation` (role_id, menu_id) VALUES (5, 26), (5, 27), (5, 28), (5, 29);

-- 如需给其它角色分配，把 role_id=5 换成对应角色 id 即可。
