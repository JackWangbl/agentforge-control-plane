# AgentScope 实战：对照本控制面由浅入深

这份文档不讲官方文档的目录，而是用 **AgentForge 控制平面里已经落地的东西**，把 AgentScope 的核心概念走一遍。读完应能回答三件事：

1. AgentScope 里那些名词，在这个项目里分别长什么样
2. 一次对话从点「发送」到回复，中间经过哪些层
3. 哪些已经接上官方能力，哪些是控制面自己先扛着、以后再换官方实现

项目入口：`app/services/agentscope_adapter.py`。架构备忘在 `docs/ARCHITECTURE.md`。

---

## 1. 先建立一张对照表

AgentScope 把「智能体应用」拆成几块积木。控制面没有重新发明一套语言，只是给每块积木加了管理界面和落库。

| AgentScope 概念 | 官方在做什么 | 本项目里的对应物 |
| --- | --- | --- |
| Agent | 有人设、能调模型、能用工具的执行单元 | 「Agent 管理」里的一条记录 + 调试台一次运行 |
| Model | 对话补全、温度、密钥 | 「模型配置」；`complete_chat()` 调 OpenAI 兼容接口 |
| Toolkit / Tool | 模型可调用的函数 | MCP 工具 + 沙箱工具 + 内置时间/计算/浏览器 |
| MCP Client | 连外部工具进程或 HTTP 服务 | `build_mcp_client()`；页面上的 HTTP Stream / SSE / StdIO |
| Skill / 提示增强 | 把专业规程喂给模型 | `skills/*/SKILL.md`，「Skill 管理」 |
| Session / Memory | 同一段对话的上下文 | 调试台工作区 + `conversations` / `chat_messages` |
| Sandbox / Runtime | 隔离执行代码 | 「沙箱管理」+ `sandbox_runtime.py` |
| Studio | 看轨迹、Token、步骤 | 「AgentScope Studio」页 + `studio_tracer.py` |
| 租户与资源可见性 | 谁能看到谁的 Agent | `app/access/`：属主 / 同租户 / 跨租户 |

记住一句就够：**AgentScope 负责“智能体怎么跑”，控制面负责“这些智能体在企业里怎么被登记、授权、观测和回归”。**

---

## 2. 最浅的一层：一次对话其实是个循环

官方 Agent 的心智模型可以压成：

```text
用户消息
  → 拼系统提示（人设 + Skill）
  → 带上工具定义，问模型
  → 模型要么直接回答，要么提出 tool_calls
  → 运行时执行工具，把结果当 role=tool 再喂回去
  → 重复若干轮，直到模型不再要工具
```

本项目把这个循环写在 `app/main.py` 的 `generate_chat_reply()` 里，没有藏在框架魔法后面，所以最适合当入门样例。

对应代码在做什么：

1. `build_system_prompt(agent, db)`  
   人设 + 已绑定 Skill 的正文。这就是 AgentScope 里「提示词 + 能力说明书」。
2. `openai_tools_for_mcps(...)`  
   把 MCP 工具编成 OpenAI `tools` 数组。模型只看 JSON Schema，不关心工具是本地函数还是远程 HTTP。
3. 若 Agent 绑了沙箱，再追加 `sandbox_run_python` / `sandbox_run_shell`。
4. `complete_chat(...)`  
   POST `{base_url}/chat/completions`。这是目前的模型通道。
5. 若有 `tool_calls`，`execute_tool(name, args, db, agent)` 真正执行，并把输出追加进 `working` 消息列表。
6. 浏览器类工具最多 8 轮，其它最多 4 轮，防止模型来回空转。

没有密钥时不会假装调用成功，而是返回预览回复。这和 AgentScope「先能跑通骨架、再接真实模型」的做法一致。

自己在调试台发一句「现在几点」，如果 Agent 绑了「本地工具」，你会在链路里看到 `get_current_time`。这就是 Toolkit 最浅的一次实战。

---

## 3. Agent：配置是资产，运行是实例

AgentScope 里，Agent 通常是代码里 new 出来的对象。企业场景里对象不能只活在内存里，所以控制面把它变成可管理的资产：

- 名称、职责、系统提示、版本、发布状态
- 用哪条模型配置
- 绑哪些 Skill、MCP、沙箱
- 独立工作目录 `workspaces/<agent>/`，会话文件和最近一次运行写在这里

**配置（Registry）和运行（Runtime）要分开想。**  
「Agent 管理」改的是配置；「调试台 / 数据测试」才是一次 Runtime 实例。同一条 Agent 配置可以被评测工人和调试台同时跑，互不影响对方的会话文件。

这和官方建议一致：Studio 里看到的是某一次 run，不是配置表本身。

---

## 4. 模型：适配层为什么要「懒加载」

`agentscope_adapter.py` 开头就写了：导入保持 lazy，控制面可以在还没配密钥、甚至没装 `agentscope` 时启动。

```text
initialize_agentscope()
  ├─ 能 import agentscope → agentscope.init(studio_url=..., tracing_url=?)
  └─ ImportError → 返回 False，控制面继续干活
```

`complete_chat()` 目前不用 AgentScope 的 Model 封装，而是直接打兼容接口。原因很实际：

- 本机 Python 可能是 3.9，硬依赖新版 AgentScope 会把整个控制面拖垮
- DashScope、DeepSeek、本地网关都只要 OpenAI 形态
- 适配层把「官方 SDK 怎么变」关在一扇门后面，页面和评测不用跟着改

等环境装好 AgentScope 2.x，可以把 `complete_chat` 换成官方 `OpenAIChatModel` / `DashScopeChatModel`，**调用方 `generate_chat_reply` 不用改形状**：仍然是 `content + tool_calls + usage`。

这就是「适配器」：对内稳定，对外可替换。

---

## 5. 工具：Toolkit、MCP、权限是三件事

初学者容易把「有个工具」理解成「模型想调就能调」。AgentScope 和本项目都拆成三步。

### 5.1 声明（给模型看）

工具要有名字、说明、参数 JSON Schema。模型只根据这些决定要不要调用。

本项目来源：

- 内置：`get_current_time`、`calculate`、`search_knowledge`、`list_agents`
- 浏览器：`browser_*`
- 远程 MCP：Streamable HTTP 探测后写入的 tools 列表
- 沙箱：仅当该 Agent 绑了启用中的策略

### 5.2 执行（给运行时做）

`execute_tool()` 按名字分发。远程工具走 `call_streamable_http_tool`；沙箱走 `run_sandbox_tool`。  
AgentScope 官方则是 Toolkit 注册函数，或 MCP Client 代调。

`build_mcp_client()` 已经按官方类型准备好了：

- HTTP / SSE → `HttpStatelessClient`
- StdIO → `StdIOStatefulClient`

传输名会做归一：页面上的「HTTP Stream」、常见的 `http`，都会变成 `streamable_http`。

### 5.3 授权（给企业用）

`agent_allows_tool()`：工具名必须出现在该 Agent 已绑 MCP 里，或者是已绑沙箱的 `sandbox_*`。  
没绑就返回「Agent 未绑定工具」。这是控制面比示例脚本多出来的一层，对应 AgentScope 资源可见性那套思路。

实战建议：先绑「本地工具」验证循环，再加远程 MCP，最后才给需要跑代码的 Agent 绑沙箱。一次加太多，链路里分不清是模型不会调还是权限拦住了。

---

## 6. Skill：不是插件，是写给模型的规程

AgentScope 生态里的 Skill，更接近「可版本化的提示词模块」，不是 Python 包。

本仓库的 `skills/customer-reply/SKILL.md`、`skills/meeting-notes/SKILL.md` 就是这种文件。控制面「Skill 管理」能预览、编辑；Agent 勾选后，`skill_prompt_block()` 把正文拼进系统提示。

因此：

- 改 Skill 立刻影响下一次对话的「性格和流程」，不必重启模型
- Skill 不会自己跑代码；要副作用，走 MCP 或沙箱
- 评测集里的期望答案，最好和 Skill 口径一致，否则包含匹配会大量失败

---

## 7. 沙箱：官方 Runtime 和本机兜底

AgentScope Runtime 的目标是：模型生成的代码不要在控制面进程里裸跑。官方路径一般是 Docker 镜像（例如 `agentscope/runtime-sandbox-base`），用 `run_ipython_cell` / `run_shell_command`。

本项目的 `sandbox_runtime.py` 按这个目标做了两级：

```text
策略.runtime 看起来像 docker / agentscope / 官方镜像
        │
        ├─ 能 import agentscope_runtime → 走官方沙箱
        └─ 否则本机隔离
              ├─ python -I 或 /bin/sh
              ├─ 超时
              ├─ 内存到了阈值就 RLIMIT_AS
              └─ deny 时注入 socket 阻断（必要时再套 sandbox-exec / unshare）
```

隔离是 **按 Agent 绑定的**，不是全局开关。`Agent.sandbox_id` 指向一条策略；工作目录按策略 ID 分开。两个 Agent 要完全隔离，就建两条策略。

「试跑代码」调用 `probe_sandbox()`：先跑 `print('sandbox-ok', 1+1)`，再试连 `1.1.1.1:53`。deny 模式下这次连接必须失败，否则卡片会告诉你「代码能跑，但网络隔离没生效」。

这是学 AgentScope Runtime 时最值得自己做一遍的实验：同一段 `import socket; socket.create_connection(...)`，在 allow 和 deny 两条策略下结果应相反。

---

## 8. Studio 与 Trace：把「黑盒一次调用」拆开

AgentScope Studio 是官方的调试 UI。控制面做了两件事：

1. 启动时 `agentscope.init(studio_url=...)`（若 SDK 可用）
2. 每次调试 / 评测后，`export_playground_to_studio()` 登记 run，并尽量打 OTLP spans

控制面自己也落一份 `traces`：`user.message` → `agent.resolve` → `skill.inject` → `mcp.bind` → `model.chat` → 各工具 → `reply.emit`。  
Studio 没开，这份本地链路仍然在「会话详情」里看得到。

学观测时建议对照三次同一句话：

1. 未绑工具：只有模型和回复
2. 绑了本地工具并问时间：中间多一个 tool span
3. 绑了沙箱并让它算 `1+1`：多 `sandbox_run_python`

这就是 AgentScope 强调 Studio 的原因——智能体的质量问题，多半出在中间某一步，而不是最后一句漂亮话。

---

## 9. 评测：把 Runtime 当成可回归的函数

官方做评测，本质是：「固定输入 → 跑同一套 Agent → 用规则或 LLM 打分」。

控制面「数据测试」就是这件事产品化：

- 数据集 = 用例表（input / expected）
- 在线抽检 = 同步 `execute_run`（条数有上限）
- 离线回归 = 写入 `queued`，工人线程领取
- 每条用例调用的仍是 `generate_chat_reply`

所以 **评测不是另一套 Agent**。你在调试台调通的绑定，评测里不会丢。反过来，评测失败时先看报告里的「实际输出」和 tool span，再改 Skill 或工具，而不是先怪模型。

打分：

- 规则分：快、稳，适合客服标准句
- LLM 分：要裁判密钥，适合开放题；裁判应尽量和业务模型分开，避免自己给自己打满分

---

## 10. 租户：AgentScope 的「可见性」在控制面怎么落地

多用户一起用控制面时，不能所有人看见所有 Agent。本项目按这三层过滤（`TenantSharePolicy`）：

1. **跨租户**：直接当不存在（演示账号 `demo` 看不见默认租户的东西）
2. **本租户 + 你是属主**：读写自己的资源
3. **本租户 + 别人的资源**：有对应 `*:read` / `*:write` 才能看或改

这和 AgentScope 把资源做成「可引用、可授权」的方向一致，只是这里落在 MySQL 的 `tenant_id` / `owner_id` 上，而不是只放在 SDK 会话里。

写扩展时不要绕过 `access_service.list_rows` / `get_row` 去 `db.query` 全表，否则隔离是假的。

---

## 11. 建议的一条实战路径

按这个顺序做，比一上来读完整 SDK 更快建立直觉。

1. **只配模型**  
   新建或选一个 Agent，调试台问一句闲聊。确认 `complete_chat` 通。

2. **只绑本地工具**  
   问时间、让它算个表达式。看链路里是否出现 tool span。

3. **绑一条 Skill**  
   用客服口径问退款。看系统提示是否变长、口吻是否跟着变。

4. **绑沙箱**  
   试跑代码应输出 `sandbox-ok 2`。再让 Agent 执行一段 Python。换 deny 策略，确认它不能联网。

5. **加远程 MCP**  
   传输选 HTTP Stream，探测成功后再绑到 Agent。同一句问话，工具名应变成远程那一侧的。

6. **做 5 条数据集**  
   在线抽检一遍。把失败的 expected 和 Skill 对齐，再跑离线。

7. **打开 Studio**  
   默认 `http://127.0.0.1:3000`。对照控制面 spans，看官方界面如何呈现同一条 run。

做到第 4 步，你已经用过 AgentScope 最关键的四块：Agent、Model、Tool、Sandbox。后面只是把它们规模化。

---

## 12. 和官方 SDK 的差距（避免误以为已经「全是 AgentScope」）

控制面是 **AgentScope 风格的管理面**，不是把官方 Runtime 每一层都替你启动好了。

已经对齐的：

- 概念划分（Agent / Tool / MCP / Skill / Studio / Sandbox / 租户）
- 启动时尝试 `agentscope.init`
- MCP 客户端按官方类去构造
- 沙箱优先走 `agentscope_runtime`
- 轨迹尽量送到 Studio

仍由控制面自己实现、以后可替换的：

- 聊天补全走裸 HTTP，不是必须经过 AgentScope Model 对象
- 本机没装官方包时，沙箱是进程级隔离，不是 Docker 强隔离
- 编排（Workflow）目前存 DAG JSON，执行引擎还薄
- 记忆主要是会话表和工作区文件，不是官方 Memory 模块的全套策略

换官方实现时，优先改 `agentscope_adapter.py` 和 `sandbox_runtime.py`，不要从页面或评测工人里直接 import `agentscope`。门留在适配层，升级才不会拆一地。

---

## 13. 收束

AgentScope 要你建立的不是「会调一次 Chat Completions」，而是：

**一个有边界的 Agent = 人设 + 模型 + 可授权工具 + 可选隔离执行 + 可观测的一次 run。**

这个控制面把这句话做成了可点击的模块。你在页面上做的每一项绑定，最后都回到 `generate_chat_reply` 那一个循环里。把这个循环看懂，再去读 AgentScope 2.0 的 Agent、Toolkit、MCP、Runtime 文档，会顺很多。
