# AgentForge Control Plane

基于 Python、FastAPI 与 AgentScope 的 Agent 管理控制面。前端由 FastAPI 同源托管，不需要单独安装 Node.js。

打开 <http://127.0.0.1:8080> 即可使用；接口文档在 `/docs`。

## 环境依赖

| 依赖 | 版本 | 说明 |
| --- | --- | --- |
| Python | 3.11+ | `pyproject.toml` 要求 `requires-python >= 3.11` |
| MySQL | 8.0+ | **必须换成你自己的实例**，字符集 `utf8mb4` |
| pip / venv | 随 Python | 安装项目依赖 |

可选：

- AgentScope Studio（默认 `http://127.0.0.1:3000`），用于看运行轨迹
- 模型供应商密钥：`DASHSCOPE_API_KEY`、`OPENAI_API_KEY`，或在「模型配置」里直接填写

自动化测试使用本地 SQLite，不依赖你的 MySQL。

## MySQL：换成自己的库

仓库里的 `.env.example` 只是占位。**不要沿用别人机器、演示环境或旧文档里的主机、账号、密码。** 自己准备一台 MySQL，建好库和用户，再写进 `.env`。

`DATABASE_URL` 格式：

```text
mysql+pymysql://用户名:密码@主机:3306/agentforge?charset=utf8mb4
```

密码里如果有 `@`、`#`、`/` 等字符，需要做 URL 编码。

### 1. 建库和账号

用管理员登录后执行（把用户名、密码改成你自己的）：

```sql
CREATE DATABASE IF NOT EXISTS agentforge
  DEFAULT CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

CREATE USER IF NOT EXISTS 'agentforge'@'%' IDENTIFIED BY '请改成你的密码';
GRANT ALL PRIVILEGES ON agentforge.* TO 'agentforge'@'%';
FLUSH PRIVILEGES;
```

只允许本机访问时，把 `'%'` 改成 `'localhost'`。

### 2. 建表

首次启动时，应用会执行 `create_all`，空库也能跑起来。若要自己先建表（给 DBA 审查，或避免启动时隐式改库），在 `agentforge` 库中执行下面的语句。

```sql
USE agentforge;

-- 租户
CREATE TABLE IF NOT EXISTS tenants (
  id INTEGER NOT NULL AUTO_INCREMENT,
  slug VARCHAR(60) NOT NULL,
  name VARCHAR(80) NOT NULL,
  description VARCHAR(200) NOT NULL,
  status VARCHAR(24) NOT NULL,
  created_at DATETIME NOT NULL,
  updated_at DATETIME NOT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY ix_tenants_slug (slug)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 角色
CREATE TABLE IF NOT EXISTS roles (
  id INTEGER NOT NULL AUTO_INCREMENT,
  name VARCHAR(60) NOT NULL,
  description VARCHAR(200) NOT NULL,
  permissions JSON NOT NULL,
  user_count INTEGER NOT NULL,
  created_at DATETIME NOT NULL,
  updated_at DATETIME NOT NULL,
  tenant_id INTEGER NOT NULL,
  owner_id INTEGER,
  PRIMARY KEY (id),
  UNIQUE KEY name (name),
  KEY ix_roles_tenant_id (tenant_id),
  KEY ix_roles_owner_id (owner_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 用户
CREATE TABLE IF NOT EXISTS users (
  id INTEGER NOT NULL AUTO_INCREMENT,
  tenant_id INTEGER NOT NULL,
  username VARCHAR(60) NOT NULL,
  display_name VARCHAR(80) NOT NULL,
  password_hash VARCHAR(200) NOT NULL,
  role_id INTEGER,
  enabled TINYINT(1) NOT NULL,
  created_at DATETIME NOT NULL,
  updated_at DATETIME NOT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY ix_users_username (username),
  KEY ix_users_tenant_id (tenant_id),
  KEY ix_users_role_id (role_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 登录令牌
CREATE TABLE IF NOT EXISTS auth_tokens (
  id INTEGER NOT NULL AUTO_INCREMENT,
  token VARCHAR(80) NOT NULL,
  user_id INTEGER NOT NULL,
  expires_at DATETIME NOT NULL,
  created_at DATETIME NOT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY ix_auth_tokens_token (token),
  KEY ix_auth_tokens_user_id (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Agent
CREATE TABLE IF NOT EXISTS agents (
  id INTEGER NOT NULL AUTO_INCREMENT,
  name VARCHAR(80) NOT NULL,
  description VARCHAR(255) NOT NULL,
  model_name VARCHAR(120) NOT NULL,
  status VARCHAR(24) NOT NULL,
  version VARCHAR(20) NOT NULL,
  system_prompt TEXT NOT NULL,
  skill_ids JSON NOT NULL,
  mcp_ids JSON NOT NULL,
  workspace VARCHAR(255) NOT NULL,
  success_rate FLOAT NOT NULL,
  created_at DATETIME NOT NULL,
  updated_at DATETIME NOT NULL,
  tenant_id INTEGER NOT NULL,
  owner_id INTEGER,
  PRIMARY KEY (id),
  UNIQUE KEY ix_agents_name (name),
  KEY ix_agents_tenant_id (tenant_id),
  KEY ix_agents_owner_id (owner_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- MCP
CREATE TABLE IF NOT EXISTS mcp_servers (
  id INTEGER NOT NULL AUTO_INCREMENT,
  name VARCHAR(100) NOT NULL,
  transport VARCHAR(30) NOT NULL,
  endpoint VARCHAR(500) NOT NULL,
  enabled TINYINT(1) NOT NULL,
  tools_count INTEGER NOT NULL,
  config JSON NOT NULL,
  created_at DATETIME NOT NULL,
  updated_at DATETIME NOT NULL,
  tenant_id INTEGER NOT NULL,
  owner_id INTEGER,
  PRIMARY KEY (id),
  UNIQUE KEY name (name),
  KEY ix_mcp_servers_tenant_id (tenant_id),
  KEY ix_mcp_servers_owner_id (owner_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Skill
CREATE TABLE IF NOT EXISTS skills (
  id INTEGER NOT NULL AUTO_INCREMENT,
  name VARCHAR(100) NOT NULL,
  description VARCHAR(300) NOT NULL,
  source VARCHAR(500) NOT NULL,
  version VARCHAR(20) NOT NULL,
  enabled TINYINT(1) NOT NULL,
  instruction TEXT NOT NULL,
  created_at DATETIME NOT NULL,
  updated_at DATETIME NOT NULL,
  tenant_id INTEGER NOT NULL,
  owner_id INTEGER,
  PRIMARY KEY (id),
  UNIQUE KEY name (name),
  KEY ix_skills_tenant_id (tenant_id),
  KEY ix_skills_owner_id (owner_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 模型配置
CREATE TABLE IF NOT EXISTS model_configs (
  id INTEGER NOT NULL AUTO_INCREMENT,
  name VARCHAR(100) NOT NULL,
  provider VARCHAR(60) NOT NULL,
  model_id VARCHAR(160) NOT NULL,
  base_url VARCHAR(500) NOT NULL,
  api_key_ref VARCHAR(160) NOT NULL,
  api_key TEXT NOT NULL,
  temperature FLOAT NOT NULL,
  enabled TINYINT(1) NOT NULL,
  created_at DATETIME NOT NULL,
  updated_at DATETIME NOT NULL,
  tenant_id INTEGER NOT NULL,
  owner_id INTEGER,
  PRIMARY KEY (id),
  UNIQUE KEY name (name),
  KEY ix_model_configs_tenant_id (tenant_id),
  KEY ix_model_configs_owner_id (owner_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 工作流
CREATE TABLE IF NOT EXISTS workflows (
  id INTEGER NOT NULL AUTO_INCREMENT,
  name VARCHAR(100) NOT NULL,
  description VARCHAR(300) NOT NULL,
  status VARCHAR(24) NOT NULL,
  graph JSON NOT NULL,
  created_at DATETIME NOT NULL,
  updated_at DATETIME NOT NULL,
  tenant_id INTEGER NOT NULL,
  owner_id INTEGER,
  PRIMARY KEY (id),
  UNIQUE KEY name (name),
  KEY ix_workflows_tenant_id (tenant_id),
  KEY ix_workflows_owner_id (owner_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 沙箱策略
CREATE TABLE IF NOT EXISTS sandbox_policies (
  id INTEGER NOT NULL AUTO_INCREMENT,
  name VARCHAR(100) NOT NULL,
  runtime VARCHAR(60) NOT NULL,
  cpu_limit VARCHAR(20) NOT NULL,
  memory_limit VARCHAR(20) NOT NULL,
  timeout_seconds INTEGER NOT NULL,
  network_mode VARCHAR(30) NOT NULL,
  enabled TINYINT(1) NOT NULL,
  created_at DATETIME NOT NULL,
  updated_at DATETIME NOT NULL,
  tenant_id INTEGER NOT NULL,
  owner_id INTEGER,
  PRIMARY KEY (id),
  UNIQUE KEY name (name),
  KEY ix_sandbox_policies_tenant_id (tenant_id),
  KEY ix_sandbox_policies_owner_id (owner_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 会话
CREATE TABLE IF NOT EXISTS conversations (
  id INTEGER NOT NULL AUTO_INCREMENT,
  session_id VARCHAR(80) NOT NULL,
  user_id VARCHAR(80) NOT NULL,
  agent_id INTEGER,
  agent_name VARCHAR(80) NOT NULL,
  title VARCHAR(200) NOT NULL,
  status VARCHAR(24) NOT NULL,
  message_count INTEGER NOT NULL,
  total_tokens INTEGER NOT NULL,
  latency_ms INTEGER NOT NULL,
  channel VARCHAR(32) NOT NULL,
  created_at DATETIME NOT NULL,
  updated_at DATETIME NOT NULL,
  tenant_id INTEGER NOT NULL,
  owner_id INTEGER,
  PRIMARY KEY (id),
  UNIQUE KEY ix_conversations_session_id (session_id),
  KEY ix_conversations_user_id (user_id),
  KEY ix_conversations_agent_id (agent_id),
  KEY ix_conversations_agent_name (agent_name),
  KEY ix_conversations_tenant_id (tenant_id),
  KEY ix_conversations_owner_id (owner_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 会话消息
CREATE TABLE IF NOT EXISTS chat_messages (
  id INTEGER NOT NULL AUTO_INCREMENT,
  session_id VARCHAR(80) NOT NULL,
  agent_id INTEGER,
  `role` VARCHAR(20) NOT NULL,
  content TEXT NOT NULL,
  agent_name VARCHAR(80) NOT NULL,
  created_at DATETIME NOT NULL,
  updated_at DATETIME NOT NULL,
  tenant_id INTEGER NOT NULL,
  owner_id INTEGER,
  PRIMARY KEY (id),
  KEY ix_chat_messages_session_id (session_id),
  KEY ix_chat_messages_agent_id (agent_id),
  KEY ix_chat_messages_tenant_id (tenant_id),
  KEY ix_chat_messages_owner_id (owner_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 链路
CREATE TABLE IF NOT EXISTS traces (
  id INTEGER NOT NULL AUTO_INCREMENT,
  trace_id VARCHAR(80) NOT NULL,
  session_id VARCHAR(80) NOT NULL,
  agent_id INTEGER,
  agent_name VARCHAR(80) NOT NULL,
  operation VARCHAR(120) NOT NULL,
  status VARCHAR(24) NOT NULL,
  duration_ms INTEGER NOT NULL,
  input_tokens INTEGER NOT NULL,
  output_tokens INTEGER NOT NULL,
  spans JSON NOT NULL,
  langfuse_url VARCHAR(500) NOT NULL,
  started_at DATETIME NOT NULL,
  tenant_id INTEGER NOT NULL,
  owner_id INTEGER,
  PRIMARY KEY (id),
  UNIQUE KEY ix_traces_trace_id (trace_id),
  KEY ix_traces_session_id (session_id),
  KEY ix_traces_agent_id (agent_id),
  KEY ix_traces_agent_name (agent_name),
  KEY ix_traces_tenant_id (tenant_id),
  KEY ix_traces_owner_id (owner_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 评测数据集
CREATE TABLE IF NOT EXISTS datasets (
  id INTEGER NOT NULL AUTO_INCREMENT,
  name VARCHAR(120) NOT NULL,
  description VARCHAR(300) NOT NULL,
  source_name VARCHAR(255) NOT NULL,
  case_count INTEGER NOT NULL,
  created_at DATETIME NOT NULL,
  updated_at DATETIME NOT NULL,
  tenant_id INTEGER NOT NULL,
  owner_id INTEGER,
  PRIMARY KEY (id),
  KEY ix_datasets_name (name),
  KEY ix_datasets_tenant_id (tenant_id),
  KEY ix_datasets_owner_id (owner_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 评测用例
CREATE TABLE IF NOT EXISTS dataset_cases (
  id INTEGER NOT NULL AUTO_INCREMENT,
  dataset_id INTEGER NOT NULL,
  case_key VARCHAR(80) NOT NULL,
  input TEXT NOT NULL,
  expected TEXT NOT NULL,
  tags JSON NOT NULL,
  extra JSON NOT NULL,
  enabled TINYINT(1) NOT NULL,
  created_at DATETIME NOT NULL,
  updated_at DATETIME NOT NULL,
  tenant_id INTEGER NOT NULL,
  owner_id INTEGER,
  PRIMARY KEY (id),
  KEY ix_dataset_cases_dataset_id (dataset_id),
  KEY ix_dataset_cases_case_key (case_key),
  KEY ix_dataset_cases_tenant_id (tenant_id),
  KEY ix_dataset_cases_owner_id (owner_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 评测任务
CREATE TABLE IF NOT EXISTS evaluation_runs (
  id INTEGER NOT NULL AUTO_INCREMENT,
  name VARCHAR(120) NOT NULL,
  dataset VARCHAR(120) NOT NULL,
  agent_name VARCHAR(80) NOT NULL,
  dataset_id INTEGER,
  agent_id INTEGER,
  judge_model_id INTEGER,
  mode VARCHAR(24) NOT NULL,
  scorer VARCHAR(24) NOT NULL,
  status VARCHAR(24) NOT NULL,
  score FLOAT NOT NULL,
  cases INTEGER NOT NULL,
  case_ids JSON NOT NULL,
  total INTEGER NOT NULL,
  passed INTEGER NOT NULL,
  failed INTEGER NOT NULL,
  skipped INTEGER NOT NULL,
  avg_latency_ms INTEGER NOT NULL,
  total_tokens INTEGER NOT NULL,
  error_message TEXT NOT NULL,
  started_at DATETIME,
  finished_at DATETIME,
  created_at DATETIME NOT NULL,
  updated_at DATETIME NOT NULL,
  tenant_id INTEGER NOT NULL,
  owner_id INTEGER,
  PRIMARY KEY (id),
  KEY ix_evaluation_runs_dataset_id (dataset_id),
  KEY ix_evaluation_runs_agent_id (agent_id),
  KEY ix_evaluation_runs_tenant_id (tenant_id),
  KEY ix_evaluation_runs_owner_id (owner_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 评测结果
CREATE TABLE IF NOT EXISTS evaluation_results (
  id INTEGER NOT NULL AUTO_INCREMENT,
  run_id INTEGER NOT NULL,
  case_id INTEGER,
  case_key VARCHAR(80) NOT NULL,
  status VARCHAR(24) NOT NULL,
  score FLOAT NOT NULL,
  input TEXT NOT NULL,
  expected TEXT NOT NULL,
  actual TEXT NOT NULL,
  reason TEXT NOT NULL,
  latency_ms INTEGER NOT NULL,
  tokens INTEGER NOT NULL,
  trace_id VARCHAR(80) NOT NULL,
  session_id VARCHAR(80) NOT NULL,
  error TEXT NOT NULL,
  created_at DATETIME NOT NULL,
  updated_at DATETIME NOT NULL,
  tenant_id INTEGER NOT NULL,
  owner_id INTEGER,
  PRIMARY KEY (id),
  KEY ix_evaluation_results_run_id (run_id),
  KEY ix_evaluation_results_case_id (case_id),
  KEY ix_evaluation_results_tenant_id (tenant_id),
  KEY ix_evaluation_results_owner_id (owner_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
```

同一份语句也在仓库根目录的 `schema.sql`，可直接导入：

```bash
mysql -h 你的主机 -P 3306 -u agentforge -p agentforge < schema.sql
```

## 配置与启动

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env
```

编辑 `.env`，至少改这两项：

```dotenv
DATABASE_URL=mysql+pymysql://用户名:密码@你的主机:3306/agentforge?charset=utf8mb4
SECRET_KEY=请改成随机长字符串
```

启动：

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8080 --reload
```

首次启动会补齐缺列、写入默认租户 / 角色 / 用户。空库即可；若已按上文建表，启动时不会重复创建同名表。

## 默认账号

| 用户名 | 密码 | 角色 |
| --- | --- | --- |
| linmo | admin123 | 平台管理员 |
| developer | dev123 | Agent 开发者 |
| auditor | audit123 | 审计员 |
| demo | demo123 | 演示租户管理员 |

上线前请改掉这些密码，并轮换 `SECRET_KEY`。

## 功能

- 会话查询、Agent 调试台、数据测试
- Agent / MCP / Skill / 模型 / 沙箱的新增、编辑、删除
- 租户隔离与 RBAC
- 可选接入 AgentScope Studio 查看轨迹

更细的模块边界见 `docs/ARCHITECTURE.md`。
