# mall

基于 Spring Boot 3 + Vue 3 + uni-app 的前后端分离商城系统，包含管理后台、H5 商城前台与配套服务端接口。

## 技术栈

| 层 | 技术 |
|---|---|
| 后端 | Java 17 · Spring Boot 3 · Spring Security · MyBatis · PageHelper |
| 存储 | MySQL · Redis · MongoDB · Elasticsearch 7.17 |
| 前端 | Vue 3 · Vite · Element Plus · uni-app |
| 部署 | Docker Compose · Nginx · Spring Cloud Gateway |

## 模块

```
mall-master/            Java 后端父工程（Maven 多模块）
├── mall-admin/         管理后台 API        :8080
├── mall-portal/        前台商城 API        :8085
├── mall-search/        Elasticsearch 搜索  :8081
├── mall-gateway/       统一入口网关        :8090
├── mall-common/        公共库
├── mall-mbg/           MyBatis 逆向工程
└── mall-security/      认证与鉴权

mall-admin-web-master/  Vue3 管理后台        :5173
mall-app-web-master/    uni-app H5 商城      :5174
deepseek_agent/         商城 AI 客服（FastAPI）:8002
deploy/                 容器编排与镜像构建
scripts/                运维与校验脚本
```

## 快速开始

### 前置

- JDK 17+、Maven 3.8+
- Node.js 18+、pnpm / npm
- MySQL 8、Redis 7、Elasticsearch 7.17（含 IK 分词插件）

### 1. 初始化数据库

```bash
mysql -uroot -p < mall-master/document/sql/mall.sql
```

### 2. 配置环境变量

敏感配置一律通过环境变量注入，仓库中不含任何真实密钥。

```bash
# Java 服务
export DB_PASSWORD=your-db-password
export JWT_SECRET=your-jwt-secret
export CORS_ALLOWED_ORIGINS=http://localhost:5173,http://localhost:5174

# AI 客服（deepseek_agent）
cp deepseek_agent/llm_backend/.env.example deepseek_agent/llm_backend/.env
# 然后编辑 .env，填入 DEEPSEEK_API_KEY 与 DB_PASSWORD
```

### 3. 启动

Windows 下可直接双击根目录的一键脚本：

| 脚本 | 作用 |
|---|---|
| `admin-web.bat` | 编译后端并启动 mall-admin |
| `mall-portal.bat` | 启动 mall-portal |
| `admin-web2.bat` | 启动管理后台前端 |
| `mall-app-web-master.bat` | 启动 H5 商城 |
| `deep.bat` | 启动 AI 客服 |

脚本都会要求先设置 `JAVA_HOME`（JDK 17+），不再写死本机路径。

Linux / macOS：

```bash
cd mall-master && mvn clean install -DskipTests
cd mall-portal && mvn spring-boot:run
cd mall-admin  && mvn spring-boot:run
cd mall-search && mvn spring-boot:run

cd mall-admin-web-master && npm install && npm run dev
cd mall-app-web-master    && npm install && npm run dev:h5
```

首次启动后需导入 ES 索引：

```bash
curl -X POST http://localhost:8081/esProduct/importAll
```

## 端口

| 服务 | 端口 |
|---|---|
| mall-admin | 8080 |
| mall-search | 8081 |
| mall-portal | 8085 |
| mall-gateway | 8090 |
| admin 前端 | 5173 |
| H5 前端 | 5174 |
| AI 客服 | 8002 |

## 安全说明

- 所有密钥经环境变量 / 本地 `.env` 注入，`.env` 已被 `.gitignore` 排除。
- 仓库中只保留 `.env.example` 模板。
- 生产环境务必显式配置 `JWT_SECRET`、`DB_PASSWORD`、`CORS_ALLOWED_ORIGINS`，否则服务按 fail-fast 策略拒绝启动。

## License

本项目仅用于学习与技术交流。
