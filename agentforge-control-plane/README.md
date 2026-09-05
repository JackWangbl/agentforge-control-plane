# AgentForge Control Plane

基于 Python、FastAPI 与 AgentScope 2.0 的企业级 Agent 管理平台起始项目。

## 已包含

- 会话检索：按 Agent 名称、用户 ID、Session ID 与状态筛选
- MCP、Skill、模型、沙箱策略与角色权限的新增和编辑
- 可拖拽的 Agent DAG 编排设计器
- 沙箱策略与运行实例管理
- RBAC 用户、角色、权限管理
- 数据集测试与评测任务
- 请求链路、Token、时延和错误监控
- AgentScope 适配层（模型、MCP、Skill、Trace 初始化入口）

## 本地启动

需要 Python 3.11+。

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env
uvicorn app.main:app --reload --port 8080
```

访问 <http://127.0.0.1:8080>。业务数据默认写入 `.env` 里配置的 MySQL；自动化测试仍使用本地 SQLite。

前端由 FastAPI 同源托管，无需单独安装 Node.js。打开首页即可操作全部管理模块；API 文档位于 `/docs`。详细的生产架构边界见 `docs/ARCHITECTURE.md`。

## 生产化建议

当前项目是可运行的管理控制面起始版本。生产环境请将 SQLite 替换为 PostgreSQL，密钥存入 KMS/Vault，沙箱执行接入 AgentScope Runtime 或 Kubernetes，并在网关层接入企业 SSO。
