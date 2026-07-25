# Phase 2 Codex 手机远程方案探索

> 当前状态：PTY 方案已于 2026-07-21 完成 macOS、Ubuntu 和手机端验收；remote-control 与 app-server 作为备选保留。

## 1. 目标

让手机通过 Chub 操作 Mac 上的原生 Codex CLI。Codex 继续负责会话、审批、命令执行、文件修改和 Git 操作；Chub 负责可信网络入口、工作区选择、会话权限模式、终端生命周期和移动端访问。

## 2. 方案对比

| 方案 | 方式 | Chub 复杂度 | 手机体验 | 状态 |
| --- | --- | ---: | ---: | --- |
| PTY / Web Terminal | 浏览器直接操作原生 Codex TUI | 中 | 已验证可接受 | 优先 |
| Codex remote-control | 使用官方远程控制与配对能力 | 低 | 待验证 | 保留 |
| Codex app-server | Chub 实现专用 Codex Web 客户端 | 高 | 可深度定制 | 保留 |

`codex exec/resume` 适合固定分析和无人交互任务，可作为补充，不作为完整手机入口的首选。

## 3. PTY 方案

### 3.1 首轮实验

2026-07-20 在 MacBook 上用 `ttyd 1.7.7` 进行了首轮实验：Tailscale IPv4 监听、固定工作目录、原生 `codex` 子进程、可写终端、单客户端。手机通过 tailnet 访问后，Codex TUI 可正常使用。

### 3.2 产品边界

Chub 不解析 Codex 的消息或审批交互，只管理：

- 工作区白名单。
- 会话的创建、进入、状态和停止。
- 会话权限模式：`Ask for approval`、`Approve for me`、`Full access` 和 `Read Only`。
- 会话与工作目录、Codex session 的对应关系。
- Tailscale 监听条件。
- 手机进入终端的页面入口。
- 失控会话的终止。

### 3.3 工作区与会话

手机不接受任意路径。Chub 维护显式工作区列表，每项至少包含 ID、名称、绝对路径和是否允许手机启动 Codex。

会话列表以本机未归档 Codex 会话为准，不区分由谁创建。首版新建入口固定为三个目录：用户目录、Workspace 和 Chub。

首版操作：

- 新建会话：在选定目录启动 Codex。
- 进入会话：连接运行中的 PTY，或恢复已停止的 Codex 会话。
- 停止会话：结束 Codex 和对应 PTY，但保留可恢复记录。
- 归档会话：停止相关进程，并通过 Codex 归档本地会话。
- 删除会话：后台维护接口仍支持停止相关进程并通过 Codex 永久删除本地历史；首页日常操作不提供删除入口。
- 切换权限：保存目标权限；如果会话正在运行，先停止当前 PTY，下一次进入时按新权限启动。
- 强制停止：正常结束失败时终止完整进程组。

### 3.4 断线恢复

正式方案采用两层恢复：

- 运行层：`ttyd → tmux → codex`，浏览器断开只结束客户端。
- 持久层：保存 Codex session ID，必要时使用 `codex resume <session_id>`。

### 3.5 Session ID 与 Hook

`SessionStart` hook 提供 `session_id`、`transcript_path`、`cwd`、`source`。Chub 通过固定 hook 绑定 `CHUB_PTY_SESSION_ID`，再用 session ID 恢复和映射会话。`UserPromptSubmit` 和 `Stop` hook 只用于记录“执行中 / 等待输入”状态。

### 3.6 Web 集成

节点主页显示会话面板，点击会话进入独立终端页。终端页使用同源代理、短期 HttpOnly 凭证和独立 base path；页面返回时恢复会话面板状态。

同一会话只保留一个活动终端连接。新设备进入时接管连接，旧页面自动返回节点页；Codex 继续在本机 tmux 中运行，不因浏览器切换而停止。

### 3.7 Tailscale 下的终端连接排查

Codex PTY 页面由 Chub 代理 ttyd 的 HTTP 资源和 WebSocket。终端是持续的双向实时传输，依赖稳定的低抖动链路。首页、文档等普通 HTTP 页面可以正常访问，并不代表终端 WebSocket 一定稳定；跨网络访问经过质量不稳定的 DERP 中继、发生高延迟、抖动、丢包、MTU 不合适或网络切换时，可能出现 ttyd 页面外壳加载但终端内容不显示、输入无响应或连接中断。Tailscale 中继可以承载 WebSocket，但不稳定的中继路径不适合保证 ttyd 的实时交互体验。

排查时优先确认：

- 浏览器网络面板中的 `/codex/.../terminal/` 是否返回成功。
- `/codex/.../terminal/ws` 是否成功升级为 `101 Switching Protocols`。
- 应用日志中的 `terminal_websocket_accepted`、`terminal_websocket_disconnected`、`terminal_websocket_failed` 和 `terminal_http_*` 记录。

日志只记录会话和连接状态，不记录 Token、Cookie 或终端输出。普通 HTTP 页面正常而 WebSocket 失败时，应优先检查 Tailscale 路径稳定性、DERP 中继、丢包和 MTU，而不是重复检查文档或首页接口。

ttyd 实时终端本身不提供轮询、终端快照或命令队列等降级模式。无法获得稳定直连或可靠中继时，
应将 Codex PTY 视为网络条件不满足；此时可以改用下方独立的快速交互入口，而不是把普通页面可访问
作为终端可用的判断依据。

### 3.8 普通网络快速交互

为适应无法稳定承载 ttyd WebSocket 的普通网络，补充一个不进入实时终端页面的快速交互入口。该能力使用 Codex CLI 的非交互恢复命令，将用户输入作为新的 turn 发送到已有 Codex session，并在任务完成后返回最终结果：

```text
普通页面输入
  → Chub 固定选择 session、工作目录和权限配置
  → codex exec resume <SESSION_ID> <PROMPT>
  → 读取 JSONL 状态和最终回答
  → 普通 HTTP 轮询任务状态并展示结果
```

#### 设计边界

- 复用已有 `codex_session_id` 和会话上下文，不通过 `tmux send-keys` 模拟终端输入。
- 权限模式由 Chub 固定映射到 Codex CLI 配置，不由客户端通过任意命令或配置传入；用户需求文本通过参数或 stdin 传递，不能拼接成 shell 命令。
- `Read Only` 适合分析和检查；`Approve for me` 可用于自动审核型后台任务；`Full access` 风险最高；`Ask for approval` 在没有 ttyd 页面时无法处理需要人工确认的请求，因此第一版直接要求进入实时终端。
- 同一 session 不允许与活动 ttyd 连接或其他快速交互任务并发执行。第一版建议活动终端存在时拒绝快速交互，避免多个 Codex 进程同时写入同一会话。
- 后台任务以 JSONL 模式运行，执行阶段通过操作日志记录，并使用最终消息文件读取最终回答；输出、输入和任务记录都必须设置大小上限，不能把完整终端内容写入运行日志。
- Chub 必须向后台进程传入现有 hook 所需的 session 绑定环境，使 `UserPromptSubmit`、`Stop` 等状态仍能回写对应 Chub session。
- 页面使用普通 HTTP 轮询任务状态，不依赖 ttyd WebSocket；任务需要持续审批、实时追问或中途输入时，提示用户进入 ttyd。

当前已实现后台任务 API、任务状态持久化、并发互斥和前端提交/结果展示。任务通过受保护的
`POST /api/codex/sessions/{session_id}/quick-interactions` 创建，再通过
`GET /api/codex/quick-interactions/{task_id}` 查询单个任务，或通过
`GET /api/codex/sessions/{session_id}/quick-interactions` 恢复该 session 的历史；任务完成后返回
Codex 的最终结果，不建立 ttyd WebSocket。任务状态文件保存在 Codex PTY 数据文件旁，服务重启时
未完成任务会标记为失败，临时执行文件只在任务运行期间存在。

首页弹窗只负责快速发布任务，提交成功后立即关闭，不要求用户停留等待。session 操作区中的
“交互记录”进入当前 session 的独立历史页面；该页面恢复通过快速交互提交的需求、状态、最终结果或失败原因，
并只在存在运行中任务且页面可见时轮询。页面使用当前标签并依赖浏览器返回，不增加专用返回按钮。
Codex CLI 以 JSONL 模式输出的 `error` 和 `turn.failed` 事件会经过长度限制和脱敏后展示，额度不足、
限流或认证失败等原因不再统一折叠为“执行失败”。

第一版的边界：Ask for approval 必须进入实时终端完成审批，因此快速交互入口对该权限模式禁用；
实时终端运行中、会话尚未启动或同一 session 已有快速交互任务时也会拒绝提交。快速交互超时为
10 分钟，单次输入最多 8000 字符，最终结果最多返回 100000 字节。操作日志记录 requested、
started、succeeded/failed 或 timed_out 状态，但不记录用户输入和结果正文。

互斥检查在用户点击“执行”时由后端重新读取 session 状态，不依赖弹窗打开时的旧状态；反向进入
实时终端时也会检查该 session 是否存在运行中的快速交互。两条入口使用同一 session 级协调锁，
避免不同设备同时提交导致的检查与启动竞态。

### 3.9 安全边界

- Chub 只监听节点的 Tailscale IP。
- ttyd 只监听本机。
- 不使用 Funnel，不提供公网或普通局域网入口。
- 首页提供四种权限模式选择，并映射到 Codex CLI 的工作区、只读和完全访问配置；具体审批交互仍由 CLI 原生界面管理。
- 终端输出不写入 Hub 活动日志。

### 3.10 验收结果

2026-07-21 已完成 macOS、Ubuntu 和手机端验收：会话新建、进入、停止、恢复和归档正常；删除后台接口保留；长输出可滚动查看；浏览器退出和跨设备接管不会停止本机 Codex；新设备进入后旧页面会自动返回节点页；节点维护、跨平台运行和移动端布局符合当前使用要求。

## 4. 结论

PTY 方案已经通过当前阶段三端验收：手机可操作 macOS 与 Ubuntu 节点上的原生 Codex TUI，且能保留会话、恢复和安全边界。实时 PTY 方向正式收尾；普通网络快速交互作为独立的补充方案继续设计，不替代 ttyd 的实时终端。remote-control 与 app-server 继续保留为备选方案。
