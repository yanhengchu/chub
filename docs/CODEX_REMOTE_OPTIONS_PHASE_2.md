# Phase 2 Codex 手机远程方案探索

> 当前状态：PTY 方案已完成首轮手机实测并作为当前优先实现方案，
> remote-control 与 app-server 作为备选方案保留。

## 1. 探索目标

目标不是在 Chub 中重新实现 Codex，而是让手机通过 Chub Web 方便地操作 Mac
上的原生 Codex CLI。Codex 继续负责会话、多轮讨论、权限切换、审批、命令执行、
文件修改和 Git 操作；Chub 只负责可信网络入口、工作区选择、终端生命周期和
移动端访问。

需要支持的主要使用方式包括：

- 在指定工作区启动或进入 Codex。
- 直接使用 Codex 原生 TUI 进行需求讨论和开发。
- 在 Codex 原生界面内选择和切换权限模式。
- 使用 Full access 完成本地修改、测试、提交和推送。
- Codex 提问或请求确认时，由用户直接在原生界面响应。
- 手机断开后能够重新进入工作状态。

## 2. 候选方案概览

| 方案 | 核心方式 | Chub 实现量 | 手机体验 | 协议成熟度 | 当前状态 |
| --- | --- | ---: | ---: | ---: | --- |
| PTY / Web Terminal | 浏览器直接操作原生 Codex TUI | 中 | 已验证可接受 | PTY 稳定 | 优先完善 |
| Codex remote-control | 使用官方远程控制与配对能力 | 低 | 待验证 | 实验性 | 保留 |
| Codex app-server | Chub 实现专用 Codex Web 客户端 | 高 | 可深度定制 | 实验性 | 保留 |

`codex exec/resume` 适合固定分析报告和无人交互任务，可以作为补充能力，但不再
作为完整手机 Codex 入口的首要候选。

## 3. PTY 方案

### 3.1 首轮实验

2026-07-20 在 MacBook 安装 `ttyd 1.7.7`，使用以下边界启动临时实验：

- 监听地址：MacBook Tailscale IPv4。
- 端口：`7681`。
- 工作目录：Chub 项目目录。
- 子进程：原生 `codex`。
- 可写终端，最多一个客户端。
- 不注册系统服务，不修改 Chub 正式路由。

手机通过 tailnet 访问后，可以正常进入 Codex TUI。多轮消息、终端输入和整体
交互方式与电脑端命令行一致，手机端体验初步可接受。

### 3.2 产品边界

正式方案中，Chub 不解析 Codex 的消息、审批或权限状态。终端内部的所有交互都
交给 Codex CLI，Chub 只管理：

- 可使用的工作区白名单。
- Codex 终端的创建、进入、状态和停止。
- 终端进程与工作区的对应关系。
- Tailscale 监听条件。
- 手机进入终端的页面入口。
- 强制结束失控或不再需要的终端进程。

终端启动命令固定为指定工作区中的 Codex，不提供用户输入任意 Shell 命令的
启动参数。Codex 正常退出后，终端会话结束，不降级为普通 Shell。

### 3.3 工作区

手机不能输入任意本机路径。Chub 维护显式工作区列表，每项至少包含：

- 稳定 ID。
- 展示名称。
- 本机绝对路径。
- 是否允许手机启动 Codex。

首版可以只支持手工配置工作区。项目目录移动后由用户更新配置，不做自动扫描。

### 3.4 终端生命周期

Chub 保存多个 PTY session，并维护 session 与 Codex 原生 session、工作目录及
终端进程之间的对应关系。首版对外展示以下终端级状态：

```text
new
stopped
running
```

不复制 Codex 内部的 ready、approval 或 permission 状态。用户通过原生 TUI
直接观察和操作这些状态。终端启动失败通过 API 错误和页面提示反馈，不额外复制
Codex 内部状态。

Session 列表在终端状态之上增加轻量 Turn 活动状态：`UserPromptSubmit` Hook
触发后显示“执行中”，`Stop` Hook 触发后显示“等待输入”；终端不运行时统一显示
“可恢复”。尚未收到 Hook、Hook 未信任或 Chub 刚重启时显示“运行中”，避免将
未知状态误报为“等待输入”。该状态不继续细分工具执行和等待审批，审批细节仍
由原生 TUI 展示。

会话列表以本机 `CODEX_HOME/sessions` 中的全部未归档 Codex session 为准，
不区分会话由 Chub、其他终端或历史版本创建。Chub 创建新会话时仍只提供用户
目录、Workspace 和 Chub 三个快捷工作目录；这不限制已有会话的工作目录。

首版交互：

- “新建 Session”：在选定工作目录中启动 Codex。
- “进入 Session”：连接仍在运行的 PTY，或恢复已经停止的 Codex session。
- “停止 Session”：结束 Codex 和对应 PTY，但保留可恢复的会话记录。
- “归档 Session”：停止相关进程，并通过 Codex 归档本地会话；归档后从当前
  列表隐藏，但仍可由 Codex 取消归档。
- “删除 Session”：停止相关进程，并通过 Codex 永久删除对应的本地会话历史。
- “强制停止”：在正常结束失败时终止完整进程组。

由于会话列表会从 Codex 本地记录重新发现，删除操作必须调用 Codex CLI 的正式
删除命令，并在界面进行不可撤销确认；只删除 Chub 映射会导致会话再次出现。
Session 列表至少展示标题、工作目录、运行状态和最后活动时间，并允许保存多个
历史 Session，不要求所有 Session 常驻运行。

### 3.5 断线恢复

临时实验当前直接运行 `ttyd → codex`。正式使用前必须验证手机切后台、关闭页面、
网络短暂中断和重新打开页面时的行为。

2026-07-20 的补充实验确认：ttyd 的 WebSocket 客户端断开后，会向当前子进程
发送 SIGHUP。即使没有使用 `--once` 或 `--exit-no-conn`，直接运行
`ttyd → codex` 也不能保证 Codex 在手机断线后继续执行。

正式方案采用两层恢复：

- 运行层使用 `ttyd → tmux → codex`。浏览器断开时只结束当前 tmux 客户端，
  tmux server 和 Codex 继续运行；重新连接后重新 attach。
- 持久层保存 Codex session ID。tmux、Codex 或 Chub 重启后，使用
  `codex resume <session_id>` 恢复对话。

macOS 和 Ubuntu 使用相同的 ttyd、tmux 与 Codex 组合，只适配依赖安装和用户
服务管理。macOS 已验证 tmux detach、reattach 和完整进程清理，Ubuntu 仍需
完成同等实机验证。

### 3.6 Codex Session ID

本机 `codex-cli 0.144.6` 实验确认，Codex 的 `SessionStart` 生命周期 hook 会
通过标准输入提供结构化字段：

- `session_id`
- `transcript_path`
- `cwd`
- `source`

首次新会话在用户真正提交第一条消息、Codex 创建持久会话时触发 hook。之后可
使用记录的 ID 执行 `codex resume <session_id>`，实验已确认恢复到同一会话。
Chub 同时扫描 `~/.codex/sessions` 发现本机全部未归档会话，并读取 Codex
只读状态库取得标题和归档状态；状态库不可用时回退到 `session_index.jsonl`
标题。恢复始终使用明确的 session ID，不使用可能关联错误会话的
`codex resume --last`。

Chub 创建自己的 Session 记录时先生成独立 ID；在用户提交第一条 Codex 消息
前，`codex_session_id` 可以为空。没有提交过消息的空 Session 不具备 Codex
历史恢复能力。实验同时确认，Chub 启动 Codex 时注入的
`CHUB_PTY_SESSION_ID` 环境变量可以被固定 hook 读取，因此即使同一工作目录
并发启动多个 Session，也能将 Codex session ID 回传到正确的 Chub Session。

正式实现提供固定、可审查的 `SessionStart`、`UserPromptSubmit` 和 `Stop`
Hook，并处理 Codex 对本地 Hook 的一次性信任确认。不能通过
`--dangerously-bypass-hook-trust` 绕过用户已有 Hook 的信任检查。Hook 回传
失败时仍允许当前 PTY 使用；Session 列表退化为终端级运行状态。

### 3.7 多工作区与并发

一个节点可以保存多个 Session，同一工作目录也可以创建多个不同 Session。
停止的 Session 不占用常驻终端进程，进入时再通过 Codex 原生 resume 恢复。

首版需要继续确定同时运行的 Session 上限。无论节点保存多少 Session，同一
运行中的终端默认只允许一个可写浏览器连接，避免两个设备同时输入。

### 3.8 Web 集成方式

正式页面采用节点能力与终端子页面结合的方式：

1. 节点主页增加“Codex PTY”能力。点击后不跳转，在现有结果区域展示 Session
   列表和新建入口。
2. 点击具体 Session 后进入独立、接近全屏的终端子页面。
3. 从终端页返回节点主页时，恢复 Codex PTY 结果区域和之前的滚动位置。

终端子路由遵循现有单节点产品形态，不额外引入全局 Session 中心。例如现有
单节点路由可以使用 `/codex/{session_id}`；如果未来路由包含节点标识，则使用
`/nodes/{node_id}/codex/{session_id}`。浏览器刷新、系统返回和手机边缘返回
应与该路由正常配合，不在终端内容区域自行实现滑动手势。

2026-07-20 的最小实验已经验证 FastAPI 可以在同一路径下代理 ttyd 的 HTML
资源和 `tty` WebSocket 子协议，并完成双向终端数据传输。正式方案因此采用：

- ttyd 只监听 loopback 随机端口或本机 Unix socket，不直接暴露到 tailnet。
- ttyd 为每个 Session 使用独立 base path。
- Chub 代理 ttyd 的 HTTP 与 WebSocket，并校验外部 Origin。
- 代理连接 ttyd 时重写为 ttyd 后端允许的 Origin。
- WebSocket 正常断开作为普通生命周期处理，不记录为应用错误。

现有页面使用 Bearer Token，浏览器原生 WebSocket 和终端页面导航不能方便地
附加该请求头。进入终端前，由受 Bearer Token 保护的 API 签发短期、绑定
Session 的 HttpOnly Cookie 或一次性 ticket；Chub 的终端 HTTP/WebSocket
代理验证该凭证。不得因为终端位于同源路径就跳过应用层认证。

### 3.9 节点页面交互

节点能力做以下收敛：

- 移除“系统检查”和“版本信息”的页面入口。
- “Codex 检查”暂时保留，用于诊断 Codex 是否安装及其版本；PTY 自身具备清晰
  诊断能力后再评估移除。
- 新增“Codex PTY”，作为主要的远程开发入口。

Codex PTY 在节点主页的结果区域展示轻量 Session 面板：

```text
Codex PTY

新建 Session
[ 用户目录 ] [ Workspace ] [ Chub ]

Sessions
● chub       运行中      刚刚使用    >
○ workspace  已停止      昨天使用    >
```

首版新建入口固定为三个目录：

- 用户目录：当前运行用户的 Home。
- Workspace：配置的工作区根目录，默认可对应 `~/workspace`。
- Chub：当前 Chub 项目目录。

路径从运行环境和配置解析，不硬编码用户名；目录不存在时入口置为不可用。点击
Session 主区域进入终端子页面；停止、归档和删除作为条目操作直接展示，删除
必须二次确认。终端页只保留轻量顶部栏，提供返回、Session 标识和连接状态，
其余空间交给 Web Terminal。

### 3.10 安全边界

- Chub 只监听节点的 Tailscale IP，ttyd 后端只监听 loopback 随机端口。
- 配置不是 Tailscale IP 时，不允许启动手机 Codex 终端。
- 不使用 Tailscale Funnel。
- 不提供普通局域网或公网监听。
- 终端子进程固定为 Codex，不提供普通 Shell 入口。
- 权限模式、Full access 和审批全部由 Codex CLI 原生界面管理，Chub 不再实现
  权限切换控件或复制权限状态。
- Chub 提供立即停止终端和终止进程组的能力。
- 终端输出不写入 Hub 活动日志。

### 3.11 手机端交互边界

首版直接采用 ttyd/xterm 与 Codex TUI 已提供的交互能力，不为手机重新实现一套
终端输入组件。Chub 负责避免双重滚动、正确调整软键盘和横竖屏下的终端尺寸，
以及返回节点页时不停止 Session。快捷键栏等体验增强只在实测证明必要后增加。

### 3.12 PTY 后续验证项

- [ ] 手机切后台后重新进入。
- [ ] 关闭页面后重新进入。
- [ ] Tailscale 短暂断线后恢复。
- [ ] Codex 权限切换和 Full access。
- [ ] Codex 提问与确认界面。
- [ ] 中文输入、复制粘贴、方向键、Escape 和 Ctrl 组合键。
- [ ] 长输出阅读、滚动和终端尺寸变化。
- [ ] Codex 退出后的终端行为。
- [ ] 强制停止后不存在残留 Codex 子进程。
- [x] 确认直接 ttyd 连接断开后会结束 Codex 子进程。
- [ ] 验证 tmux 断线保持和 Codex resume 两层恢复。
- [x] 验证通过 SessionStart hook 取得 Codex session ID 并按 ID 恢复。
- [x] 在真实 Session 中信任 `UserPromptSubmit`、`Stop` Hook，并验证
  “执行中/等待输入”状态切换。
- [x] 验证 Chub/FastAPI 可代理 ttyd HTTP 和双向 WebSocket。
- [x] 验证短期终端凭证以及 HTTP/WebSocket 鉴权。
- [x] 在 macOS 验证多个 Session 的创建、停止、恢复、归档和删除。
- [x] 在 macOS 验证三个固定工作目录及移动端列表交互。
- [x] 在 macOS 验证终端返回后恢复节点页 PTY 面板。

2026-07-20 已完成 macOS 本机实现验证：Chub 同源终端页面、HTTP/WebSocket
代理、短期 HttpOnly 凭证、ttyd→tmux→Codex 启动、WebSocket 断开后 tmux
保持，以及 Session 的恢复、归档、删除和进程清理均正常。手机端列表布局和
原生 TUI 交互已完成首轮验收，Turn Hook 的“执行中/等待输入”状态切换也已通过
真实 Session 验证。升级前已经运行的旧 Session 需要先停止再恢复，才能加载
新增 Hook；Ubuntu 实机仍需完成同等回归。

## 4. Remote-control 方案

当前 Codex CLI 提供实验性的：

```text
codex remote-control start
codex remote-control stop
codex remote-control pair --json
```

该方案可能让 Chub 只负责启动、停止、状态和配对入口，实际远程交互交给 Codex
官方客户端。需要后续验证手机端入口、本地工作区、原生权限切换、会话保持和
Full access 是否满足需求。在验证完成前不删除该候选。

## 5. App-server 方案

Codex app-server 支持 stdio、Unix socket 和 WebSocket，并可生成 TypeScript
绑定与 JSON Schema。Chub 可以基于协议实现移动端专用界面，同时继续复用 Codex
原生会话、权限和审批。

该方案对手机体验的控制力最高，但 app-server 当前为实验性能力，Chub 需要处理
协议适配、版本兼容和连接恢复。只有当 PTY 体验出现明确不足时，再评估是否值得
承担该复杂度。

## 6. 当前结论

PTY 方案已经证明手机端可以操作原生 Codex TUI，并作为当前优先实现方案。
产品交互采用“节点页 Session 面板 + 独立终端子页面”，支持三个固定目录、多个
可恢复 Session，以及停止、归档和永久删除。Session 统一发现、Session ID
hook、同源代理、短期凭证、持久化和进程清理均已实现；正式运行采用
`ttyd → tmux → codex` 和 Codex resume 两层恢复。当前主要剩余 Ubuntu 实机
回归和更长时间的手机端实际使用验证。Remote-control 与 app-server 保留为
备选，不在本轮实现。
