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
- 沙箱策略是控制面定义；当前支持一次性加固 Docker 容器，生产高安全环境应进一步接 AgentScope Runtime、Kubernetes Job 或独立容器服务。

## 多租户运行时安全边界

- Agent、Skill、MCP 和 Sandbox 的引用必须属于同一个 `tenant_id`，配置写入和运行时各校验一次。
- Agent 工具调用使用服务端创建的 `ExecutionContext`；模型输出中的租户 ID 不作为授权依据。
- Browser Context 按租户、Agent 和会话隔离，并默认拒绝本机及私有网络地址。
- Workspace 和 Sandbox 工作目录按租户分层，沙箱子进程只接收白名单环境变量。
- 宿主机本地沙箱默认关闭。仅本地开发可显式设置 `ALLOW_UNSAFE_LOCAL_SANDBOX=1`；生产环境必须接入独立沙箱运行时。
- `docker:` 运行时采用镜像白名单且禁止自动拉取，容器默认断网、根文件系统只读、删除全部 capabilities，并限制 CPU、内存、PID 和文件描述符。
- 这些措施是控制面纵深防御。数据库实例、对象存储 IAM、Kubernetes Namespace 和 NetworkPolicy 等物理边界仍需由部署层提供。

## 生产数据模型扩展

建议补充 `tenant`、`user`、`permission`、`agent_version`、`message`、`dataset_case`、`audit_log` 与 `secret_ref`，所有业务表增加 `tenant_id`，并按租户执行行级授权。
