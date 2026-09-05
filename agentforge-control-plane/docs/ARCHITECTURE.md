# 架构设计

```text
Browser Admin Console
        │ REST / SSE
FastAPI Control Plane ─── RBAC / Audit
        │
        ├── Registry: Agent / MCP / Skill / Model / Workflow
        ├── Runtime: AgentScope 2.0 / Sandbox / Session
        ├── Quality: Dataset / Evaluation / Regression Gate
        └── Observe: OpenTelemetry Trace / Metrics / Logs
                │
      MySQL + Object Storage + OTLP Backend
```

## 边界说明

- `app/main.py` 是控制面 API；生产项目建议拆分为 router、service、repository 三层。
- `app/services/agentscope_adapter.py` 隔离 AgentScope 版本变动，集中构造 Toolkit、MCP Client 与 Agent。
- `Workflow.graph` 保存 DAG JSON；执行前应完成无环、入口唯一、节点权限和资源引用校验。
- `Trace.spans` 用于本地演示；生产环境应写入 OpenTelemetry 后端，数据库只保存索引和业务标签。
- API Key 只保存环境变量或密钥系统引用，不保存明文。
- 沙箱策略是控制面定义；生产执行层应接 AgentScope Runtime、Kubernetes Job 或独立容器服务。

## 生产数据模型扩展

建议补充 `tenant`、`user`、`permission`、`agent_version`、`message`、`dataset_case`、`audit_log` 与 `secret_ref`，所有业务表增加 `tenant_id`，并按租户执行行级授权。

