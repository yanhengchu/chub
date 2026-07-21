# Phase 2 Codex 手机远程方案探索

> 当前状态：PTY 方案已于 2026-07-21 完成 macOS 和手机端验收；remote-control 与 app-server 作为备选保留。

## 1. 目标

让手机通过 Chub 操作 Mac 上的原生 Codex CLI。Codex 继续负责会话、权限、审批、命令执行、文件修改和 Git 操作；Chub 只负责可信网络入口、工作区选择、终端生命周期和移动端访问。

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

Chub 不解析 Codex 的消息、审批或权限状态，只管理：

- 工作区白名单。
- 会话的创建、进入、状态和停止。
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
- 删除会话：停止相关进程，并通过 Codex 永久删除本地历史。
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

### 3.7 安全边界

- Chub 只监听节点的 Tailscale IP。
- ttyd 只监听本机。
- 不使用 Funnel，不提供公网或普通局域网入口。
- Codex 的权限模式、Full access 和审批由 CLI 原生界面管理。
- 终端输出不写入 Hub 活动日志。

### 3.8 验收结果

2026-07-21 已完成 macOS 和手机端验收：会话新建、进入、停止、恢复、归档和删除正常；长输出可滚动查看；浏览器退出和跨设备接管不会停止本机 Codex；新设备进入后旧页面会自动返回节点页；节点任务、维护操作和移动端布局符合当前使用要求。

## 4. 结论

PTY 方案已经通过当前阶段验收：手机可操作原生 Codex TUI，且能保留会话、恢复和安全边界。后续只需完成 Ubuntu 回归和 M2 网络可达性验收；remote-control 与 app-server 继续保留为备选方案。
