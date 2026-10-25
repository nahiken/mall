-- 阶段14：管理后台新增 "Agent 工作台" 一级菜单（实时监控 / 知识库管理 / Agent 会话）
-- 执行方式：在 mall 数据库执行（如 mysql -uroot -p mall < agent_workspace_menu.sql）
-- 说明：ums_menu.name 必须与前端路由 name 对齐（agent / agentMonitor / agentKnowledge / agentChat），
--      否则 permission.ts 的 hasPermission 会把路由过滤掉、侧边栏不显示。
--      幂等：重复执行不会产生重复行（WHERE NOT EXISTS / NOT IN）。

-- 1) 一级菜单 "Agent 工作台"（parent_id=0；id 取 MAX+1 避免与历史 SQL 冲突）
INSERT INTO ums_menu (id, parent_id, create_time, title, level, sort, name, icon, hidden)
SELECT (SELECT MAX(id) + 1 FROM ums_menu), 0, NOW(), 'Agent 工作台', 0, 90, 'agent', 'agent', 0
WHERE NOT EXISTS (SELECT 1 FROM ums_menu WHERE name = 'agent');

SET @pid = (SELECT id FROM ums_menu WHERE name = 'agent' ORDER BY id LIMIT 1);

-- 2) 子菜单（父 id=@pid）
INSERT INTO ums_menu (parent_id, create_time, title, level, sort, name, icon, hidden)
SELECT @pid, NOW(), '实时监控', 1, 90, 'agentMonitor', 'agent-monitor', 0
WHERE NOT EXISTS (SELECT 1 FROM ums_menu WHERE name = 'agentMonitor');

INSERT INTO ums_menu (parent_id, create_time, title, level, sort, name, icon, hidden)
SELECT @pid, NOW(), '知识库管理', 1, 91, 'agentKnowledge', 'agent-knowledge', 0
WHERE NOT EXISTS (SELECT 1 FROM ums_menu WHERE name = 'agentKnowledge');

INSERT INTO ums_menu (parent_id, create_time, title, level, sort, name, icon, hidden)
SELECT @pid, NOW(), 'Agent 会话', 1, 92, 'agentChat', 'agent-chat', 0
WHERE NOT EXISTS (SELECT 1 FROM ums_menu WHERE name = 'agentChat');

-- 3) 给"超级管理员"角色（ums_role id=5）授权这几个菜单（幂等）
INSERT INTO ums_role_menu_relation (role_id, menu_id)
SELECT 5, id FROM ums_menu
WHERE name IN ('agent', 'agentMonitor', 'agentKnowledge', 'agentChat')
  AND id NOT IN (SELECT menu_id FROM ums_role_menu_relation WHERE role_id = 5);

-- 若需给其它角色分配，把 role_id=5 换成对应角色 id 即可。
