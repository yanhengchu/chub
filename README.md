# Chub

Chub 是一个面向个人设备的轻量管理服务；默认节点 Web 应用名为 `Hub`，可通过本机 `app.name` 和 `app.page_title` 调整页面名称。

第二阶段已于 2026-07-26 正式闭环：macOS 与 Ubuntu 基础节点能力、移动端 Web 管理页面、受控任务接口、Codex PTY 与快速交互、配置驱动自动化及前端 UI 模块化均已实现，并通过 Ubuntu、MacBook 和手机端验收。第三阶段也已闭环：OpenClaw 接入、微信 ClawBot 双向交互、微信 Chub 专用任务模式、受限 Chub 状态 Tool 和飞书群机器人 Webhook 单向通知均已实现，并在 macOS、Ubuntu 和真实微信完成核心链路验收。后续能力由真实需求驱动并单独设计，不作为现阶段遗留任务。

## 快速开始

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
chmod 600 .env
```

按平台创建本机配置：

```bash
cp config/settings.macos.example.yaml config/settings.local.yaml
# 或
cp config/settings.ubuntu.example.yaml config/settings.local.yaml
```

启动服务：

```bash
.venv/bin/python main.py
```

## 本地配置

- `HUB_TOKEN`：节点访问令牌。
- `HUB_CONFIG_FILE`：默认 `config/settings.local.yaml`。
- `security.allow_tailscale`：默认 `true`。当 `server.host` 为本机 Tailscale IP 时，来自同一 Tailnet 的真实连接可免 Hub Token；非 Tailscale 来源仍需 Token。不需要该能力时可显式设为 `false`。
- `app.page_title`：浏览器标签标题和首页顶部标题，可按节点设置，例如 `MacBook · Hub`、`Ubuntu · Hub` 或 `设备.hub`；省略时浏览器标签使用 `<app.name> 管理面板`，首页顶部使用 `app.name`。
- `.env` 和 `config/settings.local.yaml` 都只保留本机内容，已加入 Git 忽略。

两个平台模板默认只监听 `127.0.0.1`。如果需要手机远程访问，把 `server.host` 改成该节点自己的 Tailscale IP，不要改成 `0.0.0.0` 或普通局域网地址。当前部署的个人 Tailnet 只加入维护者本人控制的设备，因此这些设备整体视为可信网络边界，可同时启用 `security.allow_tailscale`。浏览器会先尝试免 Token 连接，并保留之前保存的 Hub Token 作为 Tailscale 验证失败时的回退；回退认证失败后再清除无效 Token。该模式只检查真实 socket 来源，不信任转发 Header，也不识别具体用户；如果以后允许其他人的设备加入 Tailnet，需要重新评估设备级授权和高风险操作边界。

## 后台服务

开发目录可以直接安装成当前用户的后台服务：

```bash
./scripts/chub install
```

安装后会创建 `~/.local/bin/chub` 链接。常用命令：

```bash
chub start
chub stop
chub restart
chub status
chub logs
chub uninstall
```

- macOS 使用 LaunchAgent。
- Ubuntu 使用 systemd user service。
- 服务直接依赖当前工作区和 `.venv`，移动目录后需要重新安装。
- `chub restart` 会重载配置和依赖路径。

## Codex PTY

Codex PTY 依赖 `codex`、`ttyd` 和 `tmux`。安装服务时，Chub 会从当前终端 `PATH` 探测这些程序及其目录，并写入后台服务配置。

节点页面的 `Codex PTY` 卡片支持：

- 从用户目录、Workspace、Chub 三个固定入口新建会话。
- 查看本机未归档会话。
- 进入、停止或归档会话；日常页面不提供删除入口，避免误删 Codex 历史。
- 在设置页选择新建 Session 的默认权限、模型和推理等级；权限支持 `Ask for approval`、`Approve for me`、`Full access` 和 `Read Only`，默认 `Full access`，模型与等级默认跟随 Codex。该偏好只影响当前浏览器之后新建的会话，已有会话不变。
- 区分尚未启动、运行、停止和异常等会话生命周期，以及执行中、等待输入和状态未知等活动状态；首页对执行中会话快速刷新，对运行中但状态未知的会话低频确认，进入等待输入或停止后结束轮询。
- 使用 ChatGPT 登录的 Codex CLI 会在卡片中展示额度窗口的剩余比例与重置时间；该信息独立于会话轮询，首次读取后缓存 5 分钟，手动刷新才强制更新。未登录或当前认证方式不提供额度时只显示不可用状态，不展示账号、Token 或会话内容。
- 同一 Codex session 提供“实时终端”和“快速交互”两种交互入口；首页使用显示当前模式的两态按钮，点击后直接切换为另一入口，再点击 Session 进入对应页面。新建会话可直接进入快速交互，首次 Codex CLI 任务会创建原生 Codex session，后续快速交互和实时终端继续复用该 session；`Ask for approval` 权限仍需先进入实时终端。快速交互按时间线展示共享的任务、状态、结果、置顶和通知信息；设置页保存每次首次加载及继续加载 5 条或 10 条记录的浏览器偏好，以及新建 Session 的默认权限、模型和推理等级。模型及其支持等级由本机 Codex 动态提供，选择“跟随 Codex 默认”时不增加启动覆盖；已有 Session 停止恢复后继续使用自身配置。实时终端等待输入时保持 TUI/tmux 运行并允许快速交互；实时终端执行中时拒绝快速交互，快速交互执行中时禁止进入实时终端。

节点页面同时提供操作日志和运行日志。首页可查看最近 50 或 100 行，日志详情页可按来源读取更早内容或下载经过敏感信息脱敏的当前日志文件。操作日志默认写入 `logs/operations.log`，并与应用日志一样自动轮转。

Chub 只负责新建会话的权限模式、模型、推理等级选择和会话生命周期；具体审批交互、权限显示、模型切换和命令执行仍由 Codex CLI 原生界面处理。设置页的默认权限会映射到 Codex 的工作区、只读和完全访问配置；模型与推理等级作为 Session 启动配置传入，不修改 Codex 全局配置。已创建会话继续使用自身配置，Chub 会从原生 Session 记录同步在 Codex 内发生的权限、模型和等级变化。

Codex PTY 终端通过 WebSocket 持续传输输入和输出，依赖稳定的双向实时链路。Tailscale 跨网络访问时，即使首页和文档等普通 HTTP 页面可以正常打开，如果路径经过质量不稳定的 DERP 中继、存在较高抖动、丢包、MTU 问题或网络切换，仍可能出现 ttyd 页面外壳已加载但终端内容未显示、输入无响应或连接中断。这里的限制不只是带宽问题，链路稳定性和延迟同样重要。当前产品不提供基于轮询或终端快照的非实时降级模式，Codex PTY 应优先在稳定的 Tailscale 直连或可靠网络中使用。排查时查看浏览器网络面板中 `/codex/.../terminal/ws` 是否成功升级为 `101 Switching Protocols`，并结合应用日志中的 `terminal_websocket_*` 和 `terminal_http_*` 记录判断连接或上游 ttyd 是否失败。

快速交互仅通过 `Codex CLI` 执行，沿用工作区、权限和实时终端互斥逻辑，并默认按完成效果、页面或交互变化、验证结果、验收方法及必要风险组织最终回复，避免向维护者展开无关代码细节。快速交互历史会保存用户原始提交内容和最终结果，固定交付提示不写入任务正文，运行日志与操作日志也不记录正文。网页来源任务使用全局固定微信接收人发送完成结果，账号默认选择唯一健康 ClawBot；微信 Chub 入站任务则保存本次消息的账号和发送者路由，成功、失败或超时后只向该路由原路回送。普通结果完整发送且不附加页面跳转提示；超过单条上限时按段落编号分段，最多发送 5 条，只有超过总上限时才提示到快速交互页面查看。分段中途失败会记录部分送达并停止，不自动重试。通知失败不改变任务最终状态，也不切换到全局目标。

快速交互需要重启 Chub 时，只登记一次节点级延迟重启：当前结果保存并完成通知后，如果没有其他快速交互便自动重启；否则等待节点上的其他快速交互全部结束。多个任务提出的重启会合并执行，但各自保留与同一次节点重启的关联和操作日志。待重启期间不再接收新快速交互，空闲实时终端不阻塞；页面重启操作在快速交互执行中会被拒绝。新实例必须通过本机健康接口返回新的实例 ID，才记录服务恢复并在原任务后追加 Chub 系统回复；微信 Chub 来源还会使用本次任务保存的账号和发送者原路发送独立的重启结果，页面来源不发送这条微信消息。重启通知单独记录发送状态，失败不改变任务或重启结果，也不回退全局收件人。维护者在自动重启前手动重启服务时，新实例只清除自动重启计划，不发送自动重启成功消息，也不会再次重启。任务结果会明确显示“即将重启”、等待中的任务数量或登记失败；直接使用系统服务管理器强制重启仍可能中断任务。

单任务自动延迟重启已在 macOS 完成实机验收：任务最终结果先完整展示，服务随后自动重启，新实例恢复后原时间线无需手动刷新即可出现系统回复，操作日志完整记录请求、开始和最终成功。微信 Chub 来源任务的完成结果与服务恢复后的独立重启结果均已完成真实原路回送验收。多任务合并、来源分流、健康实例确认、通知中断、待处理期间手动重启清标记及 macOS/Linux 服务管理分支已有自动化测试覆盖。

Codex 快速交互允许长任务持续执行：运行超过 10 分钟时页面提示“执行时间较长，仍在运行”，不将其误判为超时。真正的执行上限由 `codex_pty.quick_interaction_timeout_seconds` 配置，默认 `21600` 秒（6 小时），允许范围为 10 分钟至 24 小时；达到上限后才会终止进程并记录为超时。修改该配置后需要重启 Chub 服务。

## OpenClaw Gateway

微信 ClawBot 调用设备能力时，固定经过 `微信 ClawBot → OpenClaw → Chub → OpenClaw → 微信 ClawBot`：OpenClaw 负责入口身份和通道上下文，Chub 负责固定能力、安全校验和最终状态。微信 Chub 模式在模型调度前拦截，并把 Hook 提供的本次账号与发送者绑定到任务；首次提交成功时立即回复由 Chub 生成的有界脱敏任务摘要，任务成功、失败或超时后再以相同摘要原路发送最终结果。语音任务的首次成功回执还会显示微信语音识别内容，整条回执有固定上限；普通文字、重复消息和失败回执不显示识别内容。Chub 仅为提交校验读取本机通道状态，并在任务结束后调用 `openclaw message send` 原路回送，不调用 Agent，也不触发新的设备操作。同一原生 Codex Session 同时只允许一个 writer：专用 Session 状态为 `unknown` 时，微信入口会关闭其页面访问、停止残留终端并确认 writer 已释放后提交；已有快速交互、明确执行中或仍被 writer 占用时拒绝，不维护额外队列。页面来源完成通知使用全局固定接收人，账号默认选择唯一健康 ClawBot。Chub 只读复用 OpenClaw 的模型配置并直连供应商、或直接调用自身飞书通知能力，均不属于反向调用。

首页“OpenClaw 环境”卡片用于管理当前节点上的 Gateway，位于“自动化环境”之后。它展示 Gateway、消息通道和 Tailscale 访问入口，并提供固定的启动、停止和重启操作；停止和重启需要二次确认。Owner 检查结果合并到消息通道和总体状态，不单独展示身份或数量；未配置或检查失败时显示“功能受限”及受控提示。卡片还可以发起受控的微信 ClawBot 登录，在短期模态框中展示绑定二维码，并在微信要求时提交手机显示的数字验证码；重新绑定可能使同一 ClawBot 在其他设备上的服务端绑定失效。消息通道在 Gateway 就绪后独立检查，检查失败不会覆盖 Gateway 状态。Tailscale Serve 可用时，卡片只展示标准 HTTPS 访问地址，整个地址区域可点击进入控制台；本机 loopback 地址只作为排障入口，不在首页展示。所有接口均使用 Hub Token 或未被关闭的 Tailnet 可信访问，操作以 Gateway 或登录进程的最终状态而不是命令进程成功创建作为成功依据，并写入完整操作日志。

微信绑定使用固定的 `openclaw channels login --channel openclaw-weixin` 命令和单一短期登录会话。二维码只保存在 Chub 进程内存中，通过禁止缓存的受保护图片接口读取；原始命令输出、二维码内容、微信账号标识和登录凭证不会返回页面。关闭弹窗不会中断登录，显式取消、登录结束或 Chub 退出会清理二维码并终止残留进程。绑定成功只代表通道登录完成，不代表发送者配对或 Owner 权限已经配置。该页面流程已完成 MacBook、Ubuntu 的真实二维码生成、扫码绑定和基础交互验收。

该卡片不提供安装、卸载、升级、初始化配置、控制台代理、任意命令或原始日志入口，也不会向页面返回 OpenClaw 配置和凭证。OpenClaw CLI 必须能从 Chub 服务的 `PATH` 找到；macOS 和 Ubuntu 使用同一套接口，由 OpenClaw 自身管理对应的 LaunchAgent 或 systemd user service。

最近一次成功检测的 OpenClaw 展示状态按节点保存在浏览器会话中。首次进入或普通刷新首页时，页面先恢复缓存再检查最新状态；从快速交互等次级页面通过浏览器历史返回时，即使首页因 `no-store` 被重建，也只恢复缓存，不自动检查 OpenClaw。用户可通过卡片刷新按钮主动检查，启动、停止、重启及微信绑定成功仍只更新本卡片。刷新失败时保留上次结果并单独提示，退出节点或认证失效时清理缓存。

## 飞书通知

Chub 通过 `~/.config/chub/notifications/registry.yaml` 登记固定飞书目标，并从同目录 `secrets/` 下权限为 `600` 的独立文件读取 Webhook。通知模块只支持有界纯文本、已配置目标和可选人员别名；普通消息默认不提醒任何人，指定人员必须使用已配置 Open ID，`@所有人` 必须由目标显式允许且由用户本次明确要求。正文中的原始 `<at>` 标签会被转义，调用方不能提交任意 URL、Open ID、Secret 路径或飞书 JSON。

受保护的 `/api/notifications/targets` 和 `/api/notifications/send` 复用 Hub Token/Tailnet 认证。统一 OpenClaw `chub` 插件提供 `chub_send_notification`，可从 TUI 或微信向已配置群发送消息。用户在当前请求中使用“消息内容：”提供正文时，插件通过 `message_received`/`llm_input` 读取进入模型前的原文，按本轮 `runId` 短期关联，并在 `before_tool_call` 中覆盖模型生成的正文参数；原文无法取得时阻止发送，只有用户要求 AI 编写正文时才允许 `generated` 模式。该会话访问需显式启用 `plugins.entries.chub.hooks.allowConversationAccess=true`。飞书 `code=0` 对外记录为 `accepted`，只代表请求被接受；短期请求 ID 防止重复发送，操作日志不记录正文、Webhook 或完整 Open ID，底层 HTTP 成功请求也不会记录完整 Webhook URL。当前 `test` 目标的普通消息、真实 Agent 原文保护和显式 `@所有人` 已完成验收，并作为现行通知基线维护；指定人员提醒在提供 Open ID 后按需扩展。

本机 AI 可使用脱敏命令校验、查看和测试配置：

```bash
chub notification validate
chub notification list
chub notification test --target test
```

`test` 会真实发送固定测试消息。Chub 的 Codex 实时终端和快速交互 Codex 模式直接使用 `chub notification send --target <target> --message <text>`，不要再通过 `openclaw agent` 绕行；OpenClaw TUI 和微信入口使用 `chub_send_notification`。两种方式均可按需使用已配置人员别名或明确允许的 `@所有人`。

## 自动化任务

Chub 提供飞书文档下载自动化能力，复用独立 Debug Chrome 的登录状态。固定下载流程维护在随版本发布的 `config/automation_templates/feishu-document-download.yaml`；公共任务维护在随版本发布的 `config/automations.yaml`；本机任务维护在不提交的 `config/automations.local.yaml`。两类任务配置都只需填写名称和飞书 Wiki 链接，当前默认并仅支持 Markdown。首页将日常“自动化任务”与低频“自动化环境”分为两张独立卡片；后者默认折叠，统一管理浏览器账户、启动方式和站点登录状态。未启动时可在自动化环境中选择默认 Chrome 的普通 Profile 和有界面或无界面模式，默认无界面；未初始化的 Profile 会在确认默认 Chrome 已退出后通过现有 `chrome-cdp` 能力复制到独立目录。复制后的网站登录状态持续保存在 Debug Chrome 副本中，不与默认 Chrome 自动双向同步。首页还可以检查飞书登录状态、在需要登录时安全展示扫码二维码、运行任务，并在“全部任务”页面查看完整列表。详细规则见 `docs/AUTOMATION_DOWNLOAD_DESIGN.md`。

创建本机配置：

```bash
cp config/automations.example.yaml config/automations.local.yaml
```

需要多端共用的任务直接添加到 `config/automations.yaml`；仅当前节点使用的任务添加到本机配置。两个文件出现 ID 和内容完全一致的任务时会自动去重并使用公共配置；ID 相同但内容不同时会提示配置冲突，不允许本机配置静默覆盖公共任务。也可以通过统一 Runner 手动执行：

```bash
.venv/bin/python -m app.automations.command run <task-id>
```

Runner 和命令行任务不会自行启动或停止 Debug Chrome。首页及“全部任务”页面中，浏览器未启动时点击任务会按当前受管默认 Profile 的无界面模式启动浏览器，确认就绪后继续提交该任务；Profile 未初始化、端口冲突或浏览器环境正被使用时会明确失败，不会自动初始化或切换账户。飞书 Wiki Markdown 下载已经完成真实流程验收；周报整理依赖各端人员的实际完成情况，因此保留人工确认后手动执行，不接入固定时间调度。新增任务仍需逐项验收后再决定执行方式。

`V 国内业务周报` 因各端完成时间不一致而保持手动触发。本期周期固定为周一至周日：每周三开始本期处理窗口，至下周二固定报送日结束。自动化卡片以固定的“V 国内业务周报下载”为标题，并在下方展示本期周期、主文档和关联文档；周三切换到新周期后显示“待下载”，不再复用上期任务状态或材料。扩展模板通过受控飞书文档路径和标题前缀的双重匹配，分别要求产品商业化、产品 OS、运营、客户端、服务端五份本期资料，并要求一份纯“起止日期 + 第 N 周”名称的上周参考资料。上周参考会下载但不参与本期日期校验；各端周报只读取首个一级标题或首个明确日期/周期标题，单日需落在本期内，日期范围的结束日期需落在本期内，开始日期可早于本期。上周参考和五份各端周报都下载成功、且各端周报全部通过后，才整体发布到 `data/artifacts/weekly-reports/<周期>/inputs/` 并原子更新正式稿失效标记。资料尚未更新、缺少有效周期标题或必需来源时保留上一份有效输入与标记，页面显示“等待各端更新”；下载、读取、结构等真实故障仍显示失败，不会触发正式周报重新生成。输入更新后必须重新校验并确认后才能生成正式周报。

## 界面风格预览

设置页提供“界面风格”入口：`Standard`（简约标准版）是默认风格，`Cyber`（科技终端版）是可选风格。点击风格卡片主体进入固定预览，点击“应用”在当前页面立即切换；选择保存在当前浏览器并应用到首页、设置、任务、项目资料、详情、日志和快速交互等公共页面，读取失败时回退 Standard。浏览器本地偏好是主题选择来源，并同步一份不含敏感信息的主题 Cookie，供服务端在返回页面时直接设置根节点风格和 `color-scheme`，避免 Cyber 页面切换前短暂出现浅色画布。两套预览使用相同的信息结构展示常用元素，不读取节点或会话数据，也不会执行业务操作。

设置页同时保存 Cyber 代码雨的速度、整体亮度和密度偏好。雨列在每次进入页面时会随机错开位置、初始进度和轻微速度差，同一次浏览期间保持稳定；每列最多两条雨串交错循环，使下一条可在上一条完全离屏前提前进入。终端 PTY 保持独立的原生终端样式，不参与 Standard/Cyber 切换。

Standard 桌面端以 1080px 作为首页、设置、次级列表、文档详情和快速交互页面的统一最大内容宽度；手机端随屏幕宽度自然铺满。快速交互消息占可用消息区域约 90%，用户消息与助手消息分别保留约 10% 的对侧视觉空间；消息气泡、元信息和输入区在预览与真实页面之间复用相同样式。

Standard 是当前项目新增页面和新增功能的默认 UI 标准。新增界面应优先复用现有 Token、公共样式和组件，并保持相同的信息层级、卡片结构、按钮主次、状态反馈、响应式布局、键盘操作及减少动态效果支持；预览页作为典型元素的视觉与交互验收入口，不要求复制每一个业务页面。

两套风格的普通文字按钮和按钮型导航入口统一使用 14px 字号，并复用相同的主次按钮语义。Cyber 为普通操作按钮及“设置”“全部文档”“全部任务”“日志详情”等按钮型导航入口增加 `>` 提示符；页面标题、Codex Session 主体、图标按钮、弹窗关闭/取消、置顶和正文链接不强行添加。快速交互发送按钮复用次级按钮样式并保持单行；OpenClaw 网关与消息通道状态在 Cyber 下使用带发光指示点的完整状态标签，Standard 继续使用轻量指示点与文字。

Standard 与 Cyber 的完整设计规范和共同维护边界统一记录在 `docs/ARCHITECTURE_EVOLUTION_DESIGN.md` 的“Standard 与 Cyber”章节；新增页面仍默认执行 Standard，并必须确认切换到 Cyber 后不存在未适配的浅色表面、横向溢出或交互退化。

## 接口

- `/api/health`：健康检查。
- `/api/status`：节点状态。
- `/api/automations`：自动化任务状态和手动运行。
- `/api/logs`：活动日志。
- `/api/maintenance/*`：节点维护操作。
- `/api/openclaw/*`：OpenClaw Gateway 状态和受控维护操作。
- `/api/codex/*`：Codex 会话管理。

项目资料列表和设计文档详情可直接通过 Chub 地址访问，便于阅读；页面内容不要求认证，因此文档不得包含 Token、Cookie、账号信息或其他本机秘密。归档状态管理仍需 Hub Token 或未被关闭的 Tailnet 可信访问，状态保存在 `data/state/project-documents.json`。首页从未归档文档中最多展示最近更新的 5 份，不再按文档状态筛选；“全部文档”保留索引中的其他当前状态，并可筛选当前和已归档文档。Chub 仍只适合部署在可信网络中。

首页“项目文档”同时展示未归档设计资料与当前“本期周报”。本期周期固定为周一至周日：每周三切换到当周周期，并从周三至下周二始终展示同一周期；下周二为固定报送日，不受自动化或汇总实际运行日期影响。周期工作区尚未创建或正式稿尚未生成时显示“待生成”，不继续展示旧周期；首页只从 `data/artifacts/weekly-reports/<周期>/output/` 下的固定文件名读取。周报列表和详情与设计文档一样，属于可信网络内公开只读内容，不要求认证；内容不得包含凭证、账号信息、本机秘密或其他不适合直接访问的信息。

### 本机数据目录

`data/` 按保留策略分为三类，均不提交：

- `data/state/`：需要在服务重启后恢复的 Codex 会话、快速交互时间线、待重启标记、自动化状态和项目资料归档状态。
- `data/runtime/`：可再生成的 Codex 执行事件、错误输出、临时钩子、自动化锁、任务日志和短期二维码；目录为 `700`，运行附件固定为 `600`。
- `data/artifacts/`：自动化下载结果、周报原始材料和正式报告，按业务周期保留。

升级到此布局时，先等待快速交互和自动化任务结束，执行 `chub stop`、`./scripts/chub-data-migrate`，确认 `config/settings.local.yaml` 使用当前示例中的路径后执行 `chub start`。迁移命令拒绝在 Chub 仍运行时执行；Codex 和项目资料状态会按记录合并，运行附件和业务产物遇到同名目标时不会覆盖。

### 设计文档管理

README 是设计文档目录结构、状态和归档规则的维护入口；具体需求、方案和任务内容仍分别维护在对应文档中。

- **当前文档**：仍描述当前有效需求、架构、功能或操作流程。已经实现或验收不代表应归档，只要内容仍是当前基线，就保留在 `docs/`。
- **阶段记录**：阶段已经闭环，但内容仍被当前工作引用或尚未被新文档完整替代，可以暂留在 `docs/`，并明确闭环状态。
- **归档文档**：已经被后续文档替代、仅用于历史追溯时，移动到 `docs/archive/phase-N/`，在文首标明已归档及当前替代文档。归档后原则上冻结，不再追加当前实现内容。

设计文档统一登记在 `docs/design_documents.json`。新增当前文档时添加 Markdown 文件，并在索引中配置唯一的小写连字符 `id`、`title`、`summary`、`status` 和相对于 `docs/` 的 `.md` 路径；“全部文档”展示索引中的所有登记项，首页从未归档文档中选择最近更新的 5 份，不根据文件所在目录或文档状态额外筛选。`status` 统一使用 2–4 个汉字，只能选择“调研中”“待实现”“进行中”“待验收”“已验收”或“持续维护”，只用于表达阶段；具体说明写入摘要，不在标签中自定义。归档文档不保留在索引中。

归档或移动文档时，应同步更新 README 文档列表、`docs/design_documents.json` 和其他文档中的引用。索引或引用文件异常会在页面和运行日志中明确提示。

## 测试

```bash
.venv/bin/python -m pip install -e ".[test]"
.venv/bin/python -m pytest
```

## 文档

当前设计与维护文档：

- [前端 UI 模块化设计](docs/ARCHITECTURE_EVOLUTION_DESIGN.md)
- [Chub AI Session 状态模型设计](docs/AI_SESSION_STATE_DESIGN.md)
- [配置驱动的飞书文档下载自动化方案](docs/AUTOMATION_DOWNLOAD_DESIGN.md)
- [工作周报生成技能设计方案](docs/WEEKLY_REPORT_SKILL_DESIGN.md)
- [OpenClaw 与消息通道接入设计](docs/OPENCLAW_INTEGRATION_DESIGN.md)
- [微信 ClawBot Context Token 持久化 AI 补丁规范](docs/WEIXIN_CLAWBOT_CONTEXT_TOKEN_AI_PATCH.md)
- [微信 ClawBot 语音转写来源标记补丁规范](docs/WEIXIN_CLAWBOT_VOICE_TRANSCRIPT_ORIGIN_PATCH.md)
- [微信 Chub 模式设计](docs/WEIXIN_CHUB_MODE_DESIGN.md)

阶段归档：

- [第一阶段归档](docs/archive/phase-1/README.md)
- [第二阶段归档](docs/archive/phase-2/README.md)
- [第三阶段归档](docs/archive/phase-3/README.md)
