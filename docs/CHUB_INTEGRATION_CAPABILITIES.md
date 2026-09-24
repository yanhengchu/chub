# Chub 集成能力清单

> 状态：持续维护
> 主要读者：AI Agent；维护者通过与 AI Agent 协作，理解并确认本文规则。
> 本文负责：定义 Chub 的核心能力体系、两套面向人的指令体系，以及程序化集成契约；微信 ClawBot 固定指令的完整产品契约以本文第 2.2 节为准。
> 本文不负责：实现细节、身份安全、并发/持久化/调度协议字段、调用方的运行时授权或尚未实现的目标架构；这些内容由总体架构、对应专项设计和插件 README 维护。

Chub 的能力体系回答“能做什么”；指令体系回答“人如何输入文本调用”；程序化集成契约回答“系统如何调用”。当前只有两套面向人的指令体系：本机维护 CLI 与微信 ClawBot 固定指令。固定 HTTP API、工作台按钮和固定脚本均不构成第三套指令体系。微信固定指令的完整语法、用户可见行为和回复格式以第 2.2 节为准；身份、安全、并发、持久化和通知路由见对应设计文档；项目整体功能与使用方式见 [README](../README.md)。本文件描述的能力不等于所有入口均获授权：运行时能力由 Chub 根据入口、任务范围、权限和状态过滤，具体调用仍以实际校验结果为准。

## AI Agent 阅读路径

处理请求时，先在第 1 节确认能力是否已实现、当前入口是否属于允许调用面；不能从相邻能力、页面文案或未登记命令推断可用性。处理微信文字时，固定指令的语法、用户可见回复和普通任务回退只以第 2.2 节为准；不要把工作台、CLI 或 HTTP API 的行为套用到微信。处理程序化调用时只使用第 3 节已登记的契约。只有需要判断身份、路由、状态所有权、恢复或插件协议时，才沿本文件的“权威规则”链接进入专项设计；专项设计不改写本文件已定义的微信固定指令和回复格式。

Chub 的产品定位、个人工作站信任模型和插件模块通用生命周期以 [README](../README.md) 与[总体架构设计](CHUB_ARCHITECTURE_DESIGN.md)为准。本文件仅在能力表中登记它们当前对调用面和用户可见结果的影响。Deliveryline 当前实际范围以“工作台业务模块”表项和 [Deliveryline 需求交付管理平台设计](DELIVERYLINE_PLATFORM_DESIGN.md)为准：当前确认的是插件导入/移除/启用/禁用、设置页插件状态和启用后的首页入口。仓库中原有的交付线创建、AI 澄清和目标确认流程属于待重构旧原型，不登记为当前稳定能力。

## 1. Chub 能力体系

处理能力相关需求时，先定位能力域和稳定能力 ID，确认其当前状态、允许调用方和任务编排插件模块边界；需要状态机、恢复或插件协议细节时，再进入本节给出的权威规则。能力表未登记的行为不得从页面、命令或相邻能力推断为可用。

> **调用边界：能力 ID 只是稳定的产品语义目录，不是 CLI 命令、HTTP API 或可直接导入的函数。** Agent 只能使用已登记入口及其公开用例；调用方是否获授权仍由每次运行时校验决定。通用运行时发现与调用接口尚未实现，任务编排插件不能依据本表自由调用能力。

“已实现”表示 Chub 当前自身已经具备该业务能力；不表示每个入口、Agent 或插件模块都能调用。表中的能力 ID 是能力目录的稳定语义标识，不是当前 CLI、HTTP API 或可直接导入的函数。“后续可发现”表示任务编排插件模块完成运行时发现后可作为候选能力由 Chub 按任务范围、权限和状态过滤后返回；当前尚无此运行时发现或调用入口。

| 能力域 | 当前可完成的事 | 当前主要调用面 |
| --- | --- | --- | --- |
| Session 与任务 | 创建、读取、配置、提交、查询、等待、停止、归档和删除 Chub Session 与受控任务 | 工作台、快速交互、微信、受管服务 |
| Runtime 与模型 | 查询 Runtime 健康、模型、实现与用量；配置后续任务的模型、推理等级和默认实现 | 工作台、设置、微信、状态接口 |
| 文本处理、确认与通知 | 微信普通正文受控执行；投递微信回送和预配置飞书通知。旧润色与确认能力当前不可用 | 微信、Quick Worker、`chub` CLI、受信通知 API |
| 状态、资料与需求 | 查询节点状态、项目资料、受限日志、活动需求和需求归档 | 工作台、CLI、微信 |
| 今日关注 | 展示今日计划与待办；AI Runtime 可提交任务时，使用固定内部 AI Session 整理固定公开来源的 AI 动态 | 工作台今日关注页 |
| Debug Chrome、自动化与周报 | 复用受管浏览器执行受控页面操作，运行固定自动化，准备资料、生成和复核周报 | 自动化页、周报页、固定脚本与技能 |
| 服务维护与集成 | 检查、重启或恢复受管服务；查看和维护已接入的 OpenClaw、Runtime 与模块状态；在维护者明确进入时提供维护终端 | 本机 CLI、工作台、少量微信固定指令 |

### 插件模块范围

| 插件模块类型 | 当前状态 | Chub 保留的边界 |
| --- | --- | --- |
| Runtime 插件模块 | 已实现：第一方 Codex Runtime ZIP 与开发实现 | Chub 保留 Session、Worker、任务终态、维护操作和页面壳；详见 Runtime 插件模块设计。 |
| 任务编排插件模块 | 已实现：Web 与微信手动任务提交的统一空阶段分发；首个提示词优化插件已接入通用发现、协议预检、导入和未启用实现移除，尚未启用或加载；微信旧润色插件已退役 | Chub 保留入口认证、路由、幂等、目标选择、主任务投递、通知与最终状态；导入不代表插件可运行，自动化等内部入口可保持独立提交。 |
| 工作台业务模块 | 已实现首个业务模块接入：Deliveryline | Chub 提供固定页面分区、统一导入/移除/启用/禁用、首页投影和设置页插件状态；Deliveryline 的交付线、交付项和执行流程仍在产品重构范围。 |

插件模块按当前业务范围使用 Chub 能力；模块故障、未安装或停用不得阻塞 Chub 核心、其他模块或无关服务。Chub 不为普通调用设置逐项审批或额外全局门禁，但模块不能通过能力目录获得任意命令、路径、Runtime、Worker、外部通道、收件人或凭据。

### 1.1 核心能力目录

下表以稳定语义 ID 登记 Chub 的核心能力。微信、工作台、自动化等入口只是能力调用方；它们不重新定义创建 Session、提交任务或维护服务的业务能力。各 ID 的**通用运行时发现与调用接口**尚未实现，因此不能从本表推断任务编排插件模块可以按能力 ID 自由调用。当前仅启用 Web/微信普通任务的统一空阶段分发；通用任务编排阶段插件尚未启用。

| 核心能力 | 使用场景与可见结果 | 当前状态与调用方 | 任务编排插件模块 |
| --- | --- | --- | --- |
| **Session 与任务** |  |  |  |
| `chub.session.create` | 创建 Chub 逻辑 Session，返回受限 Session 引用；首次任务前不创建 Native Session | 已实现：工作台、微信 `new`、周报服务 | 后续可发现；仅在入口策略允许时创建。 |
| `chub.session.read` | 读取已授权 Session 的名称、配置、活动与受限状态 | 已实现：工作台、微信、受管服务 | 后续可发现；不暴露原生 Session 私有数据。 |
| `chub.session.configure` | 为后续任务保存模型、推理等级或允许的 Session 配置 | 已实现：工作台、设置、微信 `model` | 后续按配置权限发现；不改写已受理任务。 |
| `chub.task.submit` | 向固定或刚创建的 Chub Session 提交已登记 AI 任务，返回受限任务引用 | 已实现：快速交互、微信普通任务/续提、受管服务 | 后续可发现；只能提交 Chub 返回的受限任务意图。 |
| `chub.task.read` | 读取已授权任务的进度、结果或失败摘要 | 已实现：工作台、微信任务回送、受管服务 | 后续可发现；Worker 仍是任务终态权威。 |
| `chub.task.await` | 在任务或固定内部工作流的可信结果确认后继续受控流程 | 已实现为内部协调：受管服务及固定内部工作流 | 后续可发现；等待可信事件，不轮询任意任务。 |
| `chub.session.stop` / `chub.session.archive` / `chub.session.delete` | 停止、归档或删除 Session，并按原生终态收敛 | 已实现：工作台、微信固定指令 | 当前不授予任务编排插件；未来必须单独定义高风险授权。 |
| **Runtime 与模型** |  |  |  |
| `chub.runtime.read` | 读取 Runtime 健康、实现、模型目录和受限状态 | 已实现：工作台、设置、微信、状态接口 | 后续按只读范围发现。 |
| `chub.runtime.configure` | 配置 Runtime 启停、默认实现或节点级默认值 | 已实现：设置与受控维护入口 | 当前不授予任务编排插件。 |
| `chub.runtime.authentication.switch` | 切换 Codex Runtime 既定的 ChatGPT 账户登录与 API Key 配置，并确认认证与配置同步结果 | 已实现：自动化页、固定本机脚本与微信固定指令 | 固定指向 Codex，不跟随默认 Runtime；仅当前绑定的微信 Owner 可通过固定指令触发。其他 Runtime 只有自行实现并完成专项接入后才可提供账户操作；不授予任务编排插件、OpenClaw Agent Tool 或普通任务，不接受账号、URL、路径或凭据。 |
| `chub.usage.read` | 读取默认 Runtime 的受限用量快照，并由入口选择展示格式 | 已实现：工作台、微信、状态接口 | 只展示目标 Runtime 的实际快照；未提供或读取失败时明确不可用。后续按只读范围发现，不提供上游账户或凭据。 |
| `chub.runtime.module.manage` | 预检、导入、刷新或移除受管 Runtime 插件 | 已实现：设置受控维护入口 | 当前不授予编排插件。 |
| **文本、确认与通知** |  |  |  |
| `chub.text.process` | 微信普通正文的文本阶段处理 | 当前不可用：旧微信润色实现已移除，`text` / `text-check` 明确拒绝 | 后续按新的通用阶段协议重新设计；不得恢复旧确认队列或专属宿主。 |
| `chub.text.confirm` | 对已送达的润色结果确认、取消或后移 | 当前不可用：旧微信确认流程已退役 | 后续随新的通用文本阶段重新设计；当前不保留确认项。 |
| `chub.notification.send` | 向预配置飞书群目标投递受控文本通知，并记录飞书 Webhook 接收或状态未知 | 已实现：`chub notification …`、受信 `/api/notifications/*` | 当前不授予微信或 OpenClaw；不得指定任意收件人、URL 或凭据。 |
| **状态、资料与需求** |  |  |  |
| `chub.status.read` | 查询节点、Web、Worker 与受限运行状态 | 已实现：工作台、CLI、微信 | 后续按只读范围发现。 |
| `chub.documents.read` | 浏览已登记的项目资料与受限内容 | 已实现：工作台可信网络页面 | 当前不授予任务编排插件。 |
| `chub.requests.read` / `chub.requests.manage` | 查询、保存、更新、归档或删除活动需求 | 已实现：CLI 与微信固定指令各自开放的子集 | 当前不授予任务编排插件；写入仍须遵循需求储备规则。 |
| `chub.logs.read` | 查看或下载受限日志 | 已实现：日志页、本机 CLI | 当前不授予任务编排插件。 |
| `chub.ai_today_focus.session` | 复用一个内部 AI Session 刷新工作台当天 AI 动态 | 已实现：工作台今日关注页 | 今日关注页面始终展示今日计划和待办；AI 动态分组及其刷新入口仅在 Runtime 可提交新任务时展示。Session 创建或重建时由后端标记为 `internal`，并继承通用新会话的 Runtime、权限、模型和推理等级；列表可见性统一遵循[Chub Session 状态模型设计](AI_SESSION_STATE_DESIGN.md)。既有按当前规则创建的 Session 保留创建快照。刷新前由 Chub 核心在固定能力边界内读取四个固定来源的有界快照，初始地址与最终跳转地址均须属于对应官方域名，否则该来源按读取失败处理。Session 只根据快照总结，不能使用工具、命令或快照中的网页指令；结果标题链接必须使用对应官方来源页面中的可见详情链接，不能使用来源首页或编造链接，服务端仍会复核官方域名并回填来源名称。刷新任务以持久化操作标识精确关联 Quick Worker 任务，提交回执缺失时只在有限核验窗口内等待，不会取共享 Session 的其他任务作为结果。`read-only` Runner 的 DNS 限制不再影响该流程。旧搜索/今日关注运行状态升级时直接清空为当前空状态，不保留历史结果或旧专属权限，也不据此操作内部或 Runtime 原生 Session。今日计划和待办一期仅展示空状态，不把需求储备、Deliveryline 或 AI 推测伪装成待办。当前不授予任务编排插件、OpenClaw 或外部 Agent。 |
| **Debug Chrome** |  |  |  |
| `chub.debug_chrome.manage` | 管理受管 Debug Chrome 的状态、已初始化 Profile 与有界面/无界面实例 | 已实现：自动化页与 Chub 内置 Debug Chrome 能力 | 当前不授予任务编排插件、普通 AI Session 或外部 Agent。 |
| `chub.debug_chrome.page.read` | 使用已运行的受管 Debug Chrome 创建临时页面，读取公网 HTTP(S) 页面的最终地址、标题和有界正文快照；可复用所选 Profile 已有的网站登录态 | 已实现：Chub Session 与本机 CLI 通过固定 `chub capability page-read` 命令调用 | 不授予任务编排插件、OpenClaw、远程浏览器或其他外部 Agent；不能扩展为 Profile/CDP 控制。 |
| `chub.debug_chrome.page.interact` | 在公网 HTTP(S) 页面中按精确可见链接文字进入唯一匹配的下一页面，并返回有界正文快照；可复用所选 Profile 已有的网站登录态 | 已实现：Chub Session 与本机 CLI 通过固定 `chub capability page-interact` 命令调用 | 不授予任务编排插件、OpenClaw、远程浏览器或其他外部 Agent；不能扩展为表单填写、提交、下载或脚本操作。 |
| **自动化、周报与维护** |  |  |  |
| `chub.automation.read` / `chub.automation.run` | 查看并运行固定自动化；自动化通过 Debug Chrome 基础能力执行固定步骤 | 已实现：自动化页、受控维护入口 | 当前不授予任务编排插件。 |
| `chub.weekly_report.prepare` / `chub.weekly_report.generate` | 准备输入、生成和复核受管周报 | 已实现：周报页、固定脚本与技能 | 当前不授予任务编排插件。 |
| `chub.maintenance.check` | 只读检查本机服务、配置和受限资源 | 已实现：CLI、工作台、微信 `check` | 当前不授予任务编排插件。 |
| `chub.maintenance.recover` | 对固定服务执行重启、恢复或升级操作 | 已实现：本机 CLI、工作台、少量微信固定指令 | 当前不授予任务编排插件；不接受任意命令、路径或服务目标。 |
| `chub.deployment.release_note` | 在版本发布页复用固定内部 Session，基于最近成功发版记录、当前 HEAD 与工作区改动生成简短发版说明草稿 | 已实现：维护与版本页 | 页面打开或刷新时说明为空；AI 成功后自动填入，也可手工填写。填写说明和目标 `MAJOR.MINOR.PATCH` 版本后，确认弹窗只读显示当前 HEAD、目标 tag 的旧/新指向与实际发布类型；版本声明不一致时列出具体项。同版本重发不创建提交，更高版本由受控发布流程统一更新版本声明并创建本地版本提交。版本提交前已登记发布状态，提交后中断可明确以同一版本重试；草稿不持久化且仅由发起页面的短期令牌读取。Session 创建或重建时由后端标记为 `internal`，列表可见性统一遵循[Chub Session 状态模型设计](AI_SESSION_STATE_DESIGN.md)。Runtime 或 Worker 不可用时仍可手工填写后发布。 |
| `chub.integration.openclaw.read` / `chub.integration.openclaw.manage` | 查询受管 OpenClaw 集成状态，或在固定维护范围内配置、启动、停止和恢复 Gateway 集成 | 已实现：设置与受控维护入口 | 当前不授予任务编排插件；不提供任意 Gateway 指令、账号或路由。 |
| `chub.maintenance.terminal` | 为维护者的可信浏览器创建短期维护终端访问，并维持单一活动连接 | 已实现：工作台维护终端 | 当前不授予任务编排插件、微信、OpenClaw 或自动化；该能力等同本机用户 Shell 权限。 |

`chub.session.create`、`chub.session.read`、`chub.task.submit`、`chub.task.read` 与 `chub.task.await` 是未来任务编排插件模块通用能力发现的基础能力。其 Session 创建、Native 绑定、Worker 提交、任务终态与恢复规则分别以[Chub Session 状态模型设计](AI_SESSION_STATE_DESIGN.md)、[Chub AI Runtime 架构设计](CHUB_AI_RUNTIME_DESIGN.md)和[Chub Quick Worker 独立服务设计](CHUB_QUICK_WORKER_DESIGN.md)为权威来源；本表只定义能力语义、当前状态和开放边界。

### 1.2 Debug Chrome 基础能力

Debug Chrome 是 Chub 核心层唯一受管的浏览器执行环境。其生命周期、Profile 初始化和 Playwright/CDP 会话由 Chub 内置实现负责，并通过本机 CDP 连接提供页面会话；自动化、账号登录、Runtime 页面采集和后续业务模块都是调用方，不得各自启动浏览器、管理 Profile 或直接持有 CDP 端点。

- `chub.debug_chrome.manage` 负责 Profile、实例启动/停止、模式和最终状态；生命周期操作与 Profile 切换保持独占。
- `chub.debug_chrome.page.read` 是首个稳定的只读页面用例：调用方只能请求已运行实例读取一个公网 HTTP(S) 页面，结果为原始地址、最终地址、标题、正文和截断标记。临时页面复用所选 Profile 的既有网站登录态，因此可读取已完成登录的网站；结果不单独读取或返回 Cookie、浏览器存储、Profile 路径或 CDP 地址。读取结束后关闭自身创建的页面及其派生页面；关闭未确认时以受控错误收敛，不触碰已有页面，也不隐式启动、停止或切换浏览器。
- 页面读取使用非变更 CDP 连接，不创建保留基础页；它拒绝含凭据、非标准 HTTP(S) 端口、本机或内网地址，并在每次页面请求与最终地址上重复校验。正文与标题固定有界，读取失败以受控错误收敛，不将浏览器异常或页面敏感内容写入日志。
- `chub.debug_chrome.page.interact` 是受控的低风险浏览动作：调用方只能在临时页面中按精确可见链接文字找到唯一一个锚点，再以其公网 HTTP(S) 地址进入下一页面并取得相同的有界快照。读取结束后关闭自身创建的页面及其派生页面；关闭未确认时以受控错误收敛。页面可使用既有登录态，但它不执行页面点击事件，不接受选择器、表单值、键盘输入、脚本或下载，也不触碰已有页面。
- `chub capability page-read --url <URL>` 与 `chub capability page-interact --url <URL> --follow-link <链接文字>` 是 Chub Session 和本机 CLI 的固定本机入口，不读取任务上下文、能力 ID、Profile、CDP 地址、Cookie、脚本或选择器。CLI 在识别该资源后直接进入轻量 Python 入口，不创建临时授权文件。
- Chub 内置实现只承载固定浏览器能力，不提供 Debug Chrome Profile 或 CDP 控制权。任务编排插件、OpenClaw、远程浏览器和其他外部 Agent 不获得本机 CLI/Shell 入口，不能通过本能力访问浏览器。

需要继续了解某项能力的完整规则时，按下表定位；不要从入口命令或页面文案推断其他能力的状态、权限或恢复方式。

| 能力域 | 权威规则 |
| --- | --- |
| Session 与任务 | [Chub Session 状态模型设计](AI_SESSION_STATE_DESIGN.md)、[Chub AI Runtime 架构设计](CHUB_AI_RUNTIME_DESIGN.md)、[Chub Quick Worker 独立服务设计](CHUB_QUICK_WORKER_DESIGN.md) |
| Runtime、模型、用量与 Runtime 插件模块 | [Chub AI Runtime 架构设计](CHUB_AI_RUNTIME_DESIGN.md)、[Chub Codex Runtime 设计](CHUB_CODEX_RUNTIME_DESIGN.md)、[Chub AI Runtime 插件模块设计](CHUB_RUNTIME_PLUGIN_DESIGN.md) |
| 文本处理、确认、微信回送与通知 | 本文第 2.2 节、[OpenClaw 定制集成设计](OPENCLAW_CUSTOMIZATION_DESIGN.md)、[Chub Quick Worker 独立服务设计](CHUB_QUICK_WORKER_DESIGN.md) |
| 需求、资料、日志、自动化、周报与维护 | 本文第 2.1 节、[Chub 总体架构设计](CHUB_ARCHITECTURE_DESIGN.md)、[本期工作周报自动化与生成设计](WEEKLY_REPORT_AUTOMATION_DESIGN.md) |
| 任务编排插件模块的发现、调用、检查点与版本绑定 | [Chub 任务编排插件模块架构设计](CHUB_TASK_ORCHESTRATION_PLUGIN_DESIGN.md) |

运行时能力发现是未来任务编排插件模块的目标机制，不是当前新增入口：模块应向 Chub 查询当前任务可使用的能力集合，并只调用返回的能力；能力目录、运行时注册表与调用校验分别负责语义说明、可用性和授权，不能相互替代。

### 1.3 能力与入口关系

| 入口 | 使用场景 | 可使用的主要能力 |
| --- | --- | --- |
| 工作台与快速交互 | 维护者在可信浏览器中管理 Session、任务、设置、状态、资料和自动化 | Session/任务、Runtime 与模型、状态资料、自动化、受控维护 |
| 电脑端 `chub` CLI | 在 Chub 所在电脑安装、维护和排查服务 | 状态、通知、需求、日志、服务维护与恢复 |
| OpenClaw 微信转发插件 | 已授权微信私聊进入 Chub 固定调度链路 | 只转发微信任务，不提供 Agent Tool、状态查询或飞书通知 |
| 微信 ClawBot | 已授权 Owner 通过私聊远程使用 Chub | Session/任务、状态、需求、Codex 认证切换和限定维护；旧文本处理与确认指令仅明确拒绝 |
| 自动化与周报入口 | 维护者运行受管自动化或生成周报 | 自动化、周报、受限 AI Session/任务 |

电脑端 CLI 与微信 ClawBot 是两套独立指令：前者用于本机服务运维，后者经 OpenClaw 转发到 Chub。当前没有 npm、PyPI 或独立发行包；`chub install` 只表示从当前工作区安装本机用户服务，不表示包管理器安装。

## 2. Chub 指令体系

### 2.1 本机维护 CLI 指令体系

本机维护 CLI 是维护者在 Chub 所在设备终端输入的固定 `chub …` 命令集合，用于本机服务运维、排障、通知和需求储备；它不等同于全部核心能力，也不向微信或 OpenClaw 暴露同等权限。

#### 2.1.1 Chub CLI

| 范围 | 当前命令 |
| --- | --- |
| 项目级 | `chub`；`chub help`、`chub -h`、`chub --help`；`chub install [--force]`；`chub uninstall [--force]`；`chub status [--verbose]`；`chub check`；`chub version`、`chub --version` |
| Web | `chub web start`；`chub web stop [--force]`；`chub web restart`；`chub web logs` |
| Quick Worker | `chub worker health`；`chub worker drain`；`chub worker reload`；`chub worker recover`；`chub worker start`；`chub worker stop`；`chub worker status`；`chub worker logs` |
| 修复与重建 | `chub network restart`；`chub service definitions [--core]`；`chub runtime dependencies`；`chub workstation rebuild --force`；`chub chrome supervisor reconcile [--restart]` |
| 通知与需求 | 见第 2.1.2 节和第 2.1.3 节的 `chub notification …` 与 `chub request …` 子命令。 |

`chub help` 是当前 CLI 的无服务帮助入口。

命令统一采用“资源在前、动作在后”：项目级安装、卸载、汇总状态、检查、版本和帮助保持一级命令；Web、Worker、网络、服务定义、Runtime、升级和 Chrome Supervisor 使用分层命令。旧的连字符扁平命令不再支持。`chub status` 默认汇总 Web、Quick Worker、Debug Chrome Supervisor 与系统升级执行器的服务和最终健康状态；需要平台服务管理器原始信息时使用 `--verbose`。日志分别使用 `chub web logs`、`chub worker logs` 和 `chub upgrade logs`；升级日志在 macOS 跟随 LaunchAgent 文件日志，在 Ubuntu 跟随 `chub-system-upgrade.service` 的 user journal。`chub version` 输出当前本机版本与平台。

`chub worker reload` 会取消排队和执行中的 Worker 任务，且不会自动重放；仅应在维护者确认可中断这些任务后使用。

`chub workstation rebuild --force` 仅限本机终端，并且必须显式带 `--force`。它不是日常 Web 或 Worker 更新入口：执行器会依据当前代码、配置和 `requirements.txt` 准备 Python 环境，停止 Chub Web、Quick Worker 与受管理的 Debug Chrome Supervisor，清理 Chub 自有可重建运行态，随后建立当前默认 Runtime 并重启服务。受理、依赖检查或服务进程启动都不表示完成；只有 Web、Quick Worker、默认 Runtime 和受管理 Supervisor 的最终健康均已确认时才返回成功。执行期间的 Chub/Worker 任务会结束且不自动重放；它不拉取代码、不改写配置，也不清理 OpenClaw、原生 Runtime Session、浏览器 Profile、日志、项目资料或共享业务数据。

`chub network restart` 是 Ubuntu 专用的本机维护命令：只有
`network_recovery.enabled` 为 `true`，且 Wi-Fi 设备名、Wi-Fi UUID 与 VPN UUID 都已在本机配置中固定时才会执行。它不接受参数，不影响 Tailscale，并在 Wi-Fi 和 VPN 都被确认是 NetworkManager 活动连接后才返回成功。macOS 调用会明确失败，不执行任何网络切换。

`chub check` 是只读的完整系统检查入口，依次检查项目配置、用户服务、Web 健康、Quick Worker 健康和 `/api/status` 系统状态；任一必需检查失败时返回非零退出码，不执行重启、升级或任务清理。

`chub worker status` 只读取 Quick Worker 服务状态。`chub service definitions` 重建当前工作区对应的固定服务定义；`chub service definitions --core` 仅重建 Chub Web、Quick Worker 和升级执行器定义，供升级与恢复内部使用，不处理 Debug Chrome。核心定义的生成、Linux daemon reload 与核心服务 enable，安装前停机、升级执行器加载/启动/状态、Web/Worker 固定生命周期、Chrome Supervisor 定义/状态以及完整卸载，只在 `scripts/platform/service-management.sh` 的已枚举动作中实现；维护脚本仅编排固定 Chub 流程、运行态清理和最终确认，不直接调用服务管理器。CLI 只校验、路由并保留 Chub 运行态处理与最终健康确认。`chub runtime dependencies` 按仓库 `requirements.txt` 修复当前虚拟环境依赖；它是独立部署修复入口，不属于升级与恢复步骤。上述服务定义和依赖修复命令都不重启现有服务。`chub upgrade service` 只安装或恢复独立升级执行器，发现未完成升级时启动该执行器，不停止无关服务。`chub recovery reset --force` 是仅限本机终端的破坏性逃生入口：它不读取或校验既有升级、Session 和 Worker 状态，固定停止升级执行器、Chub Web、Quick Worker 和 Debug Chrome Supervisor，丢弃 Chub 自有可重建运行态，包括 AI Runtime、快速交互、Worker 任务、维护与延迟重启、自动化任务状态/产物/锁、Chub 插件模块安装与生命周期状态，以及部署包状态/产物，然后重建并确认 Web、Worker 和 Supervisor 健康。它不修改配置文件；正常加载仓库发布的 `config/settings.yaml`，本机 `config/settings.local.yaml` 缺失或无法读取、解析、校验时记录脱敏错误并完整忽略，继续使用默认配置。它会中断在途 Quick Worker 任务且不自动重放；不会影响 OpenClaw Gateway 状态、OpenClaw 插件或补丁、Native Session、浏览器 Profile、日志、资料、业务数据或用户配置。若当前运行态路径的类型、所有者或权限不安全，命令会在删除前失败，服务保持停止，必须先由维护者修复该本机路径。系统升级失败的操作记录是可清除的 Chub 自有运行态，下一次确认升级会清除旧记录并从当前固定方案重新开始，不兼容或续跑旧方案。`chub chrome supervisor reconcile [--restart]` 仍可单独用于只恢复 Supervisor；Ubuntu 成功同时要求 systemd active 和 Unix socket `status` 可响应，浏览器实例无需已启动；macOS 返回无需 Supervisor 操作的成功结果且不调用服务管理器。`scripts/platform/service-management.sh` 是内部固定动作适配，不是稳定 CLI，不接受任意服务名、路径或子命令。独立维护脚本的唯一正式路径是 `scripts/maintenance/`，已安装服务定义使用该路径；正式 ZIP 只携带当前正式入口，根目录旧维护与旧构建入口不再提供。上述命令均应从本机终端运行。

当前 Chub 管理的三个服务和一个第三方 Gateway 的入口边界如下；这里的“Chub 管理”是服务安装范围，不等同于三层架构中的 Chub 核心层。

| 运行部分 | 所属层 | 当前状态 | 当前入口与职责 |
| --- | --- | --- | --- |
| Chub | 核心层 | 已实现 | 由 `chub install` 与 `chub web start` 管理用户服务；提供页面、API 和快速交互入口 |
| Chub Quick Worker | AI Runtime 层 | 已实现 | 与 Chub 分离运行但由同一 CLI 安装；通过 `chub worker health` 检查；启动、停止和排空仅在本机 CLI 执行，首页受控入口仅提供重启 |
| Chub Debug Chrome | 核心层 | 已实现 | Ubuntu 由独立 Supervisor 服务持有 Debug Chrome；浏览器实例按需启动，macOS 沿用现有浏览器适配 |
| OpenClaw Gateway | 第三方服务层 | 已接入 | 第三方 Gateway、微信通道和 Chub OpenClaw 插件共同提供 ClawBot；不由 `chub web start` 启动，安装/配置以[插件说明](../integrations/openclaw/chub/README.md)为准 |

“已接入”表示 Chub 与相关通道的接口和路由已经具备，不表示本仓库包含 OpenClaw Gateway 或腾讯微信插件的源码、安装包和账号绑定流程。新设备应先按外部项目文档安装这两项，再按已生效的[插件说明](../integrations/openclaw/chub/README.md)部署 Chub 插件并完成微信验收。

当前 `chub` 命令来自仓库内的 `scripts/chub`，依赖当前工作区、`.venv` 和本机配置。项目尚未发布 npm/PyPI/独立发行包，因此 `npm install -g chub`、`pipx install chub` 和无仓库启动不属于当前可用能力。

#### 2.1.2 通知子命令

- `chub notification validate`
- `chub notification list`
- `chub notification users search --query <姓名或用户ID>`
- `chub notification test --target <target>`
- `chub notification send --target <target> --message <message>`
- `chub notification send --target <target> --message <message> --mention-all`
- `chub notification send --target <target> --message <message> --mention-recipient <recipient> [--mention-recipient <recipient> ...]`
- `chub notification send --target <target> --message <message> --mention-inline-recipient <recipient> [--mention-inline-recipient <recipient> ...]`

`--mention-recipient` 将真实 @提醒放在消息正文前；`--mention-inline-recipient` 只将消息中与所选用户展示名完全匹配的 `@姓名` 替换为真实提醒并保留原位置。每个所选用户都必须在正文中有对应标记；未登记或未选择的 `@姓名` 保持普通文本，内容仍按纯文本转义。

通知目标登记在 `~/.config/chub/notifications/registry.yaml`，Webhook 保存在
`~/.config/chub/notifications/secrets/` 下权限为 `600` 的独立文件；需要提醒的飞书用户登记在
`~/.config/chub/notifications/users.yaml`，registry 和 users 文件都必须使用 `600` 权限，两个目录必须使用 `700` 权限。目标配置保存稳定目标 ID、`display_names` 展示/别名列表、Webhook 和 `@all` 策略，不维护群成员清单。`display_names` 支持中文名称；CLI 的 `--target` 可使用目标 ID 或唯一别名，服务端最终仍按稳定目标 ID 投递。不同目标的别名不能重复，也不能与其他目标 ID 冲突。调用方只能选择预配置目标和用户 ID 并发送有界纯文本，不能指定任意 URL、Open ID、Secret 路径或凭据。

首次配置可从不含真实凭据的示例开始：

```bash
mkdir -p ~/.config/chub/notifications/secrets
chmod 700 ~/.config/chub/notifications ~/.config/chub/notifications/secrets
cp -n config/notifications.example.yaml ~/.config/chub/notifications/registry.yaml
cp -n config/notification_users.example.yaml ~/.config/chub/notifications/users.yaml
touch ~/.config/chub/notifications/secrets/test.webhook
chmod 600 \
  ~/.config/chub/notifications/registry.yaml \
  ~/.config/chub/notifications/users.yaml \
  ~/.config/chub/notifications/secrets/test.webhook
```

将完整飞书机器人 Webhook URL 作为唯一一行写入 `test.webhook`。在 `users.yaml` 中以 ASCII 用户 ID 登记中文展示名和对应 Open ID；不需要在每个群目标下重复列出群成员。`users search` 按中文展示名或 ASCII 用户 ID 返回最多 20 个候选 ID 和展示名，不返回 Open ID；同名用户必须由调用方明确选择 ID。配置文件、目录或秘密文件权限异常、内容异常或路径异常时，Chub 拒绝查询和发送，不使用旧缓存继续投递。真实 Webhook、Open ID、registry 和 users 文件不得提交到仓库。

配置后执行 `chub notification validate`、`chub notification list` 和 `chub notification test --target test`。`test` 会真实发送固定测试消息。Chub 在 `data/local/state/notifications/` 以 `600` 状态文件保存短期请求指纹和投递状态，不保存消息正文；Web 与 CLI 通过同目录的私有文件锁串行更新此状态。Webhook 超时、断链或服务中断后，同一 `request_id` 在 TTL 内返回状态未知且不会重发。飞书通知当前由 `chub notification …` 或受信通知 API 直接调用；OpenClaw 只负责微信任务转发，不调用飞书通知接口。

#### 2.1.3 需求储备子命令

- `chub request save --title <标题>`：从标准输入保存一条新需求，占用编号最小的空闲 R 槽位。
- `chub request update RN [--title <标题>]`：从标准输入整体替换活动需求正文，可同时更新标题。
- `chub request show RN`：查看一条活动需求的标题和完整正文。
- `chub request archive RN`：保存活动需求的归档快照并释放对应槽位。
- `chub request list`：按槽位列出全部活动需求。

`RN` 只接受 `R1`–`R9`。标题最多 48 字符，正文最多 2000 字符；空正文、活动槽位已满或目标不存在时明确失败。保存、更新和归档只在维护者明确要求后由本机编码 Agent 执行，不提供微信写入入口，也不得直接编辑需求状态文件。标准输入中的段落必须使用真实换行；调用方不得把换行预先编码为字面量 `\\n`，写入后应使用 `chub request show RN` 验证段落格式。本机 CLI 不提供删除子命令；删除使用第 2.2 节登记的微信固定指令。

需求储备的权威文件是 `data/shared/chub/requests.json`，归 Chub 共享资料所有，不属于 OpenClaw 私有数据。该文件可由维护者通过 Git 工作流同步；Chub 不自动执行 Git 操作。多设备修改产生未合并冲突、非法 JSON 或同步状态无法确认时，读写必须失败关闭，不覆盖其他设备内容。共享需求不得包含凭证、本机秘密或其他不应进入 Git 历史的内容。

完整安装条件、平台差异和命令说明见 [README](../README.md)。

### 2.2 微信 ClawBot 固定指令体系

微信 ClawBot 固定指令是已授权 Owner 通过微信文本输入调用受控能力的唯一语法和回复契约。它不是 CLI 的远程透传：只支持下表登记的固定指令，未登记内容按普通任务规则处理，且可调用范围独立受微信入口策略约束。固定指令统一从文本开头解析，每条消息最多识别一条指令，暂不支持复合指令。

#### 2.2.1 指令契约

| 指令 | 当前行为 |
| --- | --- |
| `chub` / `check` / `usage` | 分别只读查询 Chub 摘要、核心服务维护检查与详细完整额度 |
| `help` / `chub help`、`model help`、`session help`、`request help`、`system help` | 顶层帮助的两个写法等价；带主题时只显示该类的完整语法；均不附加状态尾部；`text help` 与其他 `text` 前缀一样明确拒绝。 |
| `text ...` / `text-check <English>` | 旧微信文本润色指令已移除；当前明确拒绝，不降级为普通任务 |
| `model` / `model list` / `model level [M#]` / `model use M# \| L# \| M# L#` | 查询或配置当前 Session 后续任务的模型与推理等级 |
| `sync` / `new [title]` / `rename <title>` / `retry` / `last` | 同步当前已允许工作区中的 Session 槽位；创建或重命名当前 Session，提交待续提任务，或重新发送上一条普通任务结果 |
| `S# [task]` | 选择目标 Session；有正文时切换后提交任务 |
| `stop [S#]` / `archive S#` / `del S#` | 停止、归档或永久删除指定 Session；`stop` 无参数时作用于当前绑定 Session |
| `cat R#` / `archive R#` / `del R#` | 查看、归档或永久删除活动需求 |
| `codex auth` / `codex auth switch` | 查看当前 Codex 认证方式，或切换到另一既定认证方式；固定使用 Codex Runtime，不受默认 Runtime 切换影响，切换通过独立通知返回最终结果 |
| `restart` / `restart web` / `restart worker` / `restart clawbot` / `restart network` / `chub rebuild` | 重启固定目标或启动 Chub 工作站重建；重建为高影响操作，先返回已受理状态 |

#### 2.2.2 语法与匹配

- 固定指令只从文本开头匹配；匹配前移除首尾空白，固定指令末尾的 Unicode 标点可忽略。没有完整匹配的内容均按普通任务发送；Unicode 符号、Emoji 和消息中段出现的指令文本不自动触发固定路由。
- Chub 在自身保留指令之后检查固定的指令组前缀目录；`codex` 仍归逻辑 Codex Runtime。`text`、`text-check` 已退役，命中这些前缀时明确拒绝，绝不回退为普通任务。
- 未命中自身保留指令或固定指令组前缀的消息，若存在缺少参数、槽位非法、附加不符合语法的正文或未知指令，不得返回 `Usage` 或执行部分指令，必须按原文作为普通任务提交。命中固定指令组前缀后的子命令错误按该组自己的 Usage 处理。只有普通消息自身为空或全为空白时才拒绝提交。
- `<value>` 表示必填参数，`[value]` 表示可选参数。
- 固定指令只接受表内英文规范格式；大小写和末尾 Unicode 标点可忽略。中文别名、中文数字、裸数字、缺少必要空格及其他旧格式均不再识别，按原文作为普通任务提交。
- 会话槽位只表示为 `S1`–`S9`，需求槽位只表示为 `R1`–`R9`；`S`、`R` 不区分大小写。
- `S1`–`S9` 是持久槽位标识，不是 Session 列表的当前排序位置；列表排序变化不改变槽位。
- `R1`–`R9` 是最多九个活动需求的真实槽位，不是更大列表的排序别名。需求操作必须保留 `R` 前缀，以避免与 Session 槽位混淆；`cat README`、`run tests` 等非需求槽位正文仍进入普通任务，不扩展为文件读取或系统命令。
- Session 规范格式为 `S# [task]`、`stop [S#]`、`archive S#` 和 `del S#`。槽位与正文之间必须使用空格；`stop` 省略槽位时使用当前绑定 Session。需求规范格式为 `cat R#`、`archive R#` 和 `del R#`，同样要求空格。
- `restart`、`restart web`、`restart worker`、`restart clawbot`、`restart network` 和 `chub rebuild` 均为精确无参数指令；大小写和首尾标点可忽略，附加任何正文时回退为普通任务。`restart` 与 `restart web` 都只作用于 Web；其余四条分别只作用于 Quick Worker、ClawBot、已配置的 Ubuntu NetworkManager Wi-Fi/VPN 或 Chub 工作站重建，不把目标名称交给客户端自由解析。
- `restart worker` 会立即登记恢复操作，取消排队任务并停止执行中任务，不自动重放；`restart clawbot` 会在当前 OpenClaw 调度请求返回后异步执行，先同步固定兼容基线，再重启 Gateway，并在最终状态确认后发送独立结果。
- `restart network` 与 `chub network restart` 共用同一 Ubuntu NetworkManager 流程，仅在本机 `network_recovery.enabled` 为 `true`、且 Wi-Fi 设备名、Wi-Fi UUID 与 VPN UUID 均已固定配置时可用；macOS 调用明确失败，不执行网络切换。该流程按固定顺序断开 VPN、关闭再打开 Wi-Fi、等待指定无线设备可用、在该设备上重新连接 Wi-Fi 和 VPN，最后确认两者都是 NetworkManager 活动连接。它不接受连接名、UUID、设备名、命令或路径，也不控制 Tailscale。Wi-Fi 断开期间即时回执可能无法送达；最终结果只沿本次保存的微信路由回送，Wi-Fi 或 VPN 任一终态无法确认即记录失败而不伪报成功。
- `codex auth` 与 `codex auth switch` 均为精确无参数指令；大小写和首尾标点可忽略，附加任何正文返回 Codex 指令组 Usage，不提交普通任务。前者只返回当前已确认的 `ChatGPT account signed in`、`API Key mode enabled` 或 `Authentication status unavailable`。后者仅在当前模式已确认时执行既有认证切换脚本，并自动切到另一种既定模式；它不接受模式、账户、设备码、URL、路径或凭据。切换期间另一条切换指令明确拒绝，不因 Session 或任务状态阻断；脚本仍独占自动化和 Debug Chrome，完成后确认认证终态、配置同步与浏览器恢复。首次回复为已开始，最终结果只沿本次保存的微信路由回送；任一最终确认缺失均不得报为切换成功。
- `chub rebuild` 不接受版本号、路径或其他参数；它启动独立的 Chub 工作站重建操作，不拉取代码或接受调用方指定的维护目标。首次回复只确认已启动，说明 Chub 可能短暂不可用，并提示稍后发送 `check` 查看；旧 Chub 任务会结束，不恢复、不重放，也不发送重建完成通知。重建只导入并启用当前默认 Runtime，不恢复旧 Runtime 选择或可选插件。旧 `rebuild` 与 `upgrade` 不再是固定指令，按普通正文处理。
- `usage` 是精确无参数指令；大小写和首尾标点可忽略，附加任何正文时回退为普通任务。
- `model` 是精确无参数指令；大小写和首尾标点可忽略，附加任何正文时回退为普通任务。它返回当前绑定 Session 最近一次已确认的 active 模型和推理等级；active 值缺失时继续读取该 Session 配置与 Runtime 模型目录默认值。若当前 Session 已为后续任务保存不同的模型或等级，只为不同字段额外显示 `Next model` 或 `Next level`，不把保存成功误报为已运行任务已切换。Runtime 目录无法读取或仍未提供某字段时才显示 `Default`，当前 Session 不存在或无法读取时失败关闭，不附加 Session 列表或额度尾部。
- `model list` 是精确无参数指令；它显示当前 Session 为下一任务配置的模型，并以 `M1`…列出 Runtime 模型目录中当前可用的模型 ID。列表用于帮助选择，但不是后续切换的前置步骤；目录或当前模型无法确认时失败关闭，不返回残缺列表。
- `model level` 可不带参数，或携带 `M#`。无参数时显示当前 Session 为下一任务配置的模型、当前等级及该模型的 `L1`…列表；带 `M#` 时按本次请求读取的当前 Runtime 目录选择目标模型，并只显示其 `L#` 列表而不切换。无需先执行 `model list`；索引、目录、模型、当前等级或等级列表无法确认时失败关闭。
- `model use M# | L# | M# L#` 是精确参数指令。每次请求直接读取当前 Runtime 模型目录解析索引，无需先执行 `model list` 或 `model level`：仅模型时使用目标模型声明的默认等级；仅等级时保持当前模型；二者同时提供时，`L#` 按该目标模型本次可用等级列表解析并原子保存。切换任意时刻都只影响后续任务，不改变已经受理、排队或运行任务的配置快照；成功回执显示实际保存的下一任务模型和等级。目录可能在两次消息之间变化，因此回执是最终选择依据；目录、索引、兼容性或保存结果不能确认时失败关闭，不部分更新、不猜测且不接受原始模型或等级 ID。
- `text`、`text-check` 及其子命令已退役；命中任一前缀均回复 `Text processing is unavailable. Please submit a normal task instead.`，不回退为普通正文，也不读取、创建或恢复确认项、专属模型设置和内部 Session。
- `codex` 指令组固定使用 Codex Runtime 的当前实现，不跟随全局默认 Runtime。Codex Runtime 未导入、停用或不可用时，`codex` 前缀返回对应状态，不读取认证、不启动认证切换，也不提交普通任务；可用时由 Codex Runtime 解析 `codex auth` 与 `codex auth switch`。
- `help` 与 `chub help` 是等价的精确顶层帮助指令；`model help`、`session help`、`request help` 和 `system help` 是精确主题帮助指令。`text help` 与其他 `text` 前缀一样明确拒绝。顶层帮助只显示符号说明、常用指令和主题入口；主题帮助只展示本类完整语法。旧的 `help <topic>` 和未知帮助主题按原文作为普通任务提交。
- 无参数指令必须整句匹配。`S#` 后的剩余内容始终作为普通任务正文；例如 `S2 retry` 是切换并提交正文 `retry`，不会触发续提指令。`new retry` 作为普通任务，不创建 Session 或续提任务。
- 只有表内英文规范格式属于固定指令。未登记的 `sn ...`、`session ...` 形式、旧别名和旧槽位写法均作为普通任务，不猜测为固定指令。
- 指令解析必须整体判定槽位；`S10` 等不匹配后作为普通任务，不得误解析成 `S1` 加正文。
- `new` 无标题时使用创建后的默认 Session 名称；提供标题时标题不能为空且最多 48 个字符。`rename` 的标题不能为空且最多 48 个字符。`S#` 仅在槽位后存在正文时提交任务；`stop` 可省略槽位但不接受任务正文；`archive`、`del`、四条维护指令、续提指令和 `last` 不接受任意附带正文。
- `cat R#`、`archive R#` 和 `del R#` 不接受附带正文；缺失、连续多位、越界或非 R 英文需求槽位时匹配失败并作为普通任务提交。需求标题最多 48 字符，正文最多 2000 字符。

#### 2.2.3 Session 与任务行为

以下内容只保留会影响微信用户操作、可见结果或验收的规则；身份、路由、持久化和恢复实现以引用的专项设计为准。

##### Session、任务与模型

- 页面与微信手动任务提交均先经过统一任务编排分发器的空阶段链，再由分发器作为唯一物理主任务 writer 调用 Quick Worker。自动化、周报等内部入口不因本次改造改变。
- 内部 Session 的后端类型标记、统一列表可见性开关、设置位置及 Native Session 隔离，以[Chub Session 状态模型设计](AI_SESSION_STATE_DESIGN.md)为准。内部 Session 创建时继承通用新会话配置，调整默认值不追溯改写既有 Session 的任务配置。
- 旧翻译 Session 不属于当前能力；升级时直接清除其 Chub 自有运行态，不提供显示开关或兼容读取。
- 当默认 AI Runtime 被设置页停用时，微信 ClawBot 的新任务固定回复 `Not submitted · The default AI Runtime is disabled. Chub is in base mode. Enable it in Settings to submit AI tasks.`；`chub` 状态摘要在 `Issues` 中显示 `AI Runtime is disabled. Chub is in base mode.`。这不取消已受理任务，也不影响既有 Session 的维护指令。
- 当 Quick Worker 当前不可用时，微信 ClawBot 的 `new` 和普通任务固定回复 `Not submitted · Quick Worker is unavailable. Try again later.`；维护恢复指令仍按各自契约可用。
- 当 Quick Worker 提交回执暂时无法确认时，微信回执以 `Submission is being verified by Quick Worker. Do not resend yet.` 开头，并保留当前 Session/Task 上下文。这不是任务失败：Chub 先进行有限次主动核验，随后持续以同一任务 ID 对账或幂等补交，Web 重启后继续；确认接收后继续交付，只有 Worker 明确确认未接收时才允许重试。

- 微信和 ClawBot 为普通 Chub Session 分配 S1–S9 槽位。可进入微信范围的目录仅包括当前微信主工作区和设置中显式配置的额外工作区；用户目录与 Chub 项目目录不会因内置存在而自动加入。目录仍在当前配置范围内但暂时不可用时，已分配槽位会保留并显示为不可用，拒绝新任务；只有归档或删除才释放这类槽位。将额外工作区从设置移除属于主动撤销范围：下一次同步会移除其微信槽位，Session 本身不被删除。微信 `new` 与首页新建使用同一种 Chub Session，不存在 Session 类型切换。

##### 文本处理与确认

- `text` / `text-check` 及旧微信润色设置页已移除。新的通用阶段实现完成前，命中这些指令只返回明确拒绝，不创建阶段请求、不创建内部 Session、不进入确认队列，也不提交原文。
- 普通提交路径中，当前 Session 忙时拒绝提交，并短期保存最近一次待续提正文。
- `last` 只读取当前 Quick Worker 保留的最近 30 条任务记录窗口，并在当前绑定 Session 内选择最近一条状态已收敛为成功、失败或超时的普通任务，按当前可信微信路由重新发送结果；不扫描已淘汰的历史记录，也不区分原任务来自微信、电脑端还是 Web。取消、运行中和没有终态结果的任务不参与筛选。窗口内没有符合条件的任务时明确回复未找到。长结果继续使用既有固定分段和总条数上限；重复同一微信消息不会重复发送；投递状态无法确认时不宣称成功，需发送新的 `last` 重试。
- 普通任务、切换后提交和续提任务成功后，回执只显示状态、本次可信目标 Session 与对应 `Task`；当前绑定继续使用 `▶` 标记。
- `new [title]` 创建成功后即选中新 Session；无标题时保留创建后的默认名称，提供标题时再执行命名；新建 Session 使用设置页保存的当前节点默认 Runtime、权限、模型和推理等级。已有 Session 不受新默认变化影响。
- `stop [S#]` 先回复已安排，再异步取消目标 Session 中当前由 Quick Worker 执行的任务；省略槽位时目标为当前绑定 Session。原生 Session 被外部进程占用、占用状态未知或当前没有执行任务时拒绝。最终结果只发送到本次保存的微信路由。停止不释放槽位，只有归档或删除操作释放槽位。
- `archive S#` 仅在明确知道 Session 正处于执行中时拒绝，并与 Web 使用同一条归档流程；状态未知时先交给 native session 尝试归档，由 native 返回可行性和最终结果。有原生 Session 时，原生失败或结果未知则保留 Chub 记录、任务和槽位并返回原因；原生归档成功后再清理记录并释放槽位。没有原生 Session 时直接完成 Chub 侧清理。若原生归档已完成但 Chub 清理或槽位同步中断，保留记录并允许重试，重试先确认原生已归档，不重复执行原生归档。
- `del S#` 永久删除目标 Session 及其 Chub 记录；删除前先取消可取消的 Quick Worker 任务，再由 Runtime 确认原生删除，成功后释放槽位。外部占用、运行态或结果未知时失败关闭并保留槽位；删除结果未知时不得宣告成功。
- 重复消息不重复执行。重复回执发送前刷新当前标记；槽位已释放或复用时移除不再可信的 Session 行。
- 插件等待 Chub 超时时只说明提交状态未知，不生成 Session、任务摘要或成功结论。

##### 需求储备

- 需求讨论继续使用普通 Session，不创建隐藏或专用需求 Session。微信不提供保存、更新指令；维护者明确要求后，由当前编码 Agent 按 `AGENTS.md` 使用本机受控入口保存或整体更新，不能直接编辑状态文件，也不能因普通讨论自动保存或开始实现。
- 新需求占用编号最小的空槽位，最多九个活动需求；更新不改变槽位。`chub`只在自身状态摘要中列出活动需求标题，其他完整 Session 回执不追加 Requests。无活动需求时显示单行`No requests`，读取失败显示`Requests`和`Unavailable`，不得误报为空。
- `check` 只读取当前 Web 进程的 Chub 就绪状态、Quick Worker 私有健康状态和系统内存/磁盘指标，返回 `Check · <耗时>`、`【服务】`、`【资源】` 和 `【结果】` 四段固定摘要；它不附加 Session、任务上下文或额度尾部，不调用任意命令或路径，不检查并修改服务管理器状态，不重启服务、不升级系统、不清理任务。输出不得包含 PID、generation、主机路径、凭证或其他敏感信息。
- `cat R1-R9` 完整返回标题、`Ready` 状态和正文，不附加 Session/用量尾部。
- `archive R1-R9` 保存归档快照并释放槽位；活动槽位随后可被新需求复用，旧需求槽位不再指向归档内容。当前最多保留最近 100 个归档快照，首版不提供归档查询、恢复、搜索或排序指令。
- `del R1-R9` 直接删除活动需求并释放需求槽位，不创建归档快照；删除后该槽位可被新需求复用。

#### 2.2.4 回复格式

| 规则 | 契约 |
| --- | --- |
| 固定文案 | 默认使用英文；任务标题保留来源原文，Session 名称按任务保存的 `session_id` 在展示时读取当前值 |
| 帮助清单 | `help` 与 `chub help` 显示相同的高频状态、Session、认证和维护指令：`chub · check · usage · last · sync · new [title] · S# [task]`、`codex auth · codex auth switch`、`stop [S#] · retry · archive S# · del S#`、`chub rebuild`；下方 `More commands` 保留五个主题入口。所有主题统一使用 `<topic> help`；各主题帮助分别显示本类完整语法，标题统一为 `Commands · <Topic>`；标题与每项均为独立段落 |
| Session 行 | `[▶ ]S<槽位>[ !] · [<工作区>] <标题>`；工作区名称来自 Session 已保存的展示名，最大显示宽度为 12，超出以 `…` 截断；`▶` 仅表示当前绑定，`!` 表示不可用或状态未知 |
| Task 行 | `Task · <摘要>`；`chub` 与所有携带 Session 状态的固定指令，对全部运行中的标准快速任务统一展示已脱敏、受长度限制的摘要，包含 Web 和微信入口，不按微信路由隔离；任务快照读取失败或无匹配摘要时使用 `Task · Running`；无 Task 行表示没有运行任务 |
| Request 行 | `R<槽位> · <标题>`用于`chub`列表和需求查询结果 |
| 成功任务回执 | `状态`、可信目标 Session、Task 依次使用独立段落；无法确认目标时省略 Session |
| `new` 成功回执 | `Create: S# created and selected.`；下方 `Sessions` 列表展示当前标记和 Session 名称，不在状态行重复名称 |
| `chub` 状态回执 | 首行使用本地 `node.name`，格式为 `<node.name> chub · <耗时>`；当前微信绑定 Session 已确认 active 模型和推理等级时，列表标题为 `Sessions · <model> · <level>`，否则保留 `Sessions` |
| 失败任务回执 | `状态`、可信目标 Session、Task 依次使用独立段落；无法确认目标时省略 Session |
| 段落 | Session、Task、结果正文和帮助项不得用可能被电脑微信折叠的单换行分隔 |
| 空状态 | 无 Session 时显示`No sessions`；无活动需求时显示`No requests`；读取失败显示对应`Unavailable`或`Usage unavailable`；仅已取得额度响应但缺少 Weekly 窗口时显示`Weekly Unavailable` |
| 列表截断 | 未展示数量使用 `<N> more Sessions` |
| `check` 回执 | 返回 `Check · <耗时>`，按“服务 / 维护 / 资源 / 结果”分段展示 Chub Web、Quick Worker、AI Runtime、升级与恢复和脱敏资源明细；不附加任务、Session 或用量上下文。AI Runtime 被停用、未配置、不可用或状态无法确认只作为待处理提示，不把已就绪的 Web/Worker 误判为故障；升级失败或不可用、Web/Worker/系统检查失败才使核心服务检查未通过。 |

Session 标题与任务摘要的显示规则：

- `session_name_max_width` 默认 30，`task_name_max_width` 默认 64；两项允许范围均为 4–96。
- 工作区标签固定最大显示宽度为 12，不占用 Session 标题的 `session_name_max_width`；历史 Session 缺少展示名时回退保存目录的末级名称，再回退工作区 ID。
- 半角字符按宽度 1，汉字与全角字符按宽度 2；Emoji 和组合字符保持完整字形。
- 超出显示宽度时预留 `…`；原始 Session 标题和任务摘要另有 48 字符安全上限。

#### 2.2.5 状态尾部与通知

| 回复类型 | Session/用量状态尾部 |
| --- | --- |
| `help`、`Usage` 用法错误 | 不附加 |
| `usage` | 只返回默认 Runtime 的详细完整格式，不附加 Session 状态 |
| `model`、`model list`、`model level`、`model use` | 只返回模型查询或配置结果，不附加 Session 列表或用量状态 |
| `check` | 不提交 AI 任务，也不附加 Session、用量或 `Weekly` 状态尾部 |
| `codex auth` / `codex auth switch` 及其失败 | 不附加；切换首次回复与最终通知均只描述认证流程结果 |
| `cat R#`、`archive R#`及其失败 | 不附加 |
| `del S#`、`del R#`及其失败 | 不附加 |
| `stop [S#]` 的首次受理或进行中回复 | 不附加 |
| 四条维护指令的首次 `Scheduled` 回复 | 只返回 Scheduled，不附加 Session/Task 状态和用量 |
| `restart web` 已在进行中或同步失败的回复 | 按现有固定指令规则附加可用状态 |
| `chub rebuild` | 不附加 Session/用量状态；直接返回工作站重建受理结果 |
| 切换并提交任务、续提任务 | 不附加 |
| 其他固定指令结果 | 附加 Session 状态和默认 Runtime 的短格式 |
| 主任务成功通知 | 只在结果底部追加默认 Runtime 的短格式，不附加完整 Session 状态 |
| 主任务失败、超时通知 | 不附加 |
| Web、Worker、ClawBot 重启的独立完成通知 | 附加操作后的最终状态；受理或进程启动不视为完成 |

本节只定义微信入口选用的格式及读取失败时如何降级：精确 `usage` 固定使用详细完整格式；其他允许附加额度的微信结果固定使用短格式，且短格式最多保留两个额度区块，优先级为 5h、Weekly、Today；微信不使用长格式。外部工作台右上角使用长格式，由[Chub 前端 UI 模块化设计](FRONTEND_UI_DESIGN.md)维护其页面归属。当前 Codex 的字段、单位、三种格式与缺失项处理以[Chub Codex Runtime 设计](CHUB_CODEX_RUNTIME_DESIGN.md)为准。默认 Runtime 未提供用量快照或读取失败时统一显示 `Usage unavailable`；未来默认 Runtime 提供快照时，必须先由对应 Runtime 专属设计定义其展示和账户恢复边界，再同步复检本节的入口和通知契约。

- 尾部读取失败只降级对应状态，不得覆盖指令本身的成功或失败语义。所有微信回执的额度读取超时、异常或空结果固定显示 `Usage unavailable`，不误报为 Weekly 窗口异常；`Weekly Unavailable` 只用于已取得额度响应但缺少 Weekly 窗口的场景。
- 异步主任务保存固定 `session_id`；`Started`、完成和失败通知发送时按持久化目标 ID 读取当前槽位与 Session 名称。槽位已释放或复用时标记 `Unavailable`，不得把新 Session 显示成原任务目标。
- 主任务终态继续使用 `Done`、`Failed` 或 `Timed out`，`Task · <摘要>` 必须来源于实际提交文本。微信 ClawBot 任务正常成功链路通常产生 `Started` 和 `Done` 两次异步通知；Web 快速交互任务默认只在页面时间线展示结果，不主动回送 ClawBot，但当前绑定 Session 的维护者可通过 `last` 主动请求回送窗口内最近一条普通任务结果。两者不设置到达顺序门禁，极快任务允许偶发轻微乱序。
- 失败任务页面时间线明确显示错误来源：`Chub` 表示 Chub/Worker/解析边界错误，`上游 Runtime`表示目标 Runtime 子进程提供的原始诊断；该标签不识别具体 Runtime 身份。微信完成通知标题统一使用 `Failed`，不追加错误来源；错误正文仍按 Worker 固定上限脱敏并以纯文本发送。`error_source=runtime` 保持 Runtime 通用语义，新增 Runtime 不得要求主模块修改展示标签。
- 普通任务结果通知失败保留在后台任务状态和运行日志中，不在 `chub` 中长期展示。
- 重启、停止结果通知失败会影响维护操作终态判断，继续在 `chub` 的 Issues 中展示。

#### 2.2.6 调整指令时的同步清单

新增、删除或修改固定指令时，必须同时完成：

1. 更新本节的指令表、语法边界、业务行为和回复规则。
2. 同步 Chub 指令解析、顶层及主题 `help`、成功/失败业务测试及普通任务回退测试；涉及模块指令时，额外验证“模块未导入返回模块未导入提示且不提交普通任务”与“模块已导入但登记、加载、解析或结果校验不可用时必须失败关闭”两个边界。
3. 检查 [OpenClaw 定制集成设计](OPENCLAW_CUSTOMIZATION_DESIGN.md)是否受到影响；只有身份、路由、并发、持久化或通知边界变化时才更新设计正文，不复制本节的指令表和格式规则。
4. 验证英文规范格式、`S1`–`S9` 与 `R1`–`R9` 槽位、必要空格边界、旧格式普通任务回退、幂等重放、段落换行和状态尾部。
5. 涉及调度协议、插件配置或交付决定时，再按 [Chub OpenClaw 插件说明](../integrations/openclaw/chub/README.md)完成协议升级清单、构建和部署验证。

身份、权限、并发、持久化、路由和通知安全边界以 [OpenClaw 定制集成设计](OPENCLAW_CUSTOMIZATION_DESIGN.md)为准。

## 3. 程序化集成契约

程序化集成契约供已批准的系统按固定协议调用 Chub，不接受自由文本指令；它们不构成第三套面向人的指令体系。工作台内部 API、固定执行脚本与未来能力编排 Host 不在本节完整列举。

### 3.1 OpenClaw 插件

| 插件 | 状态 | 功能 |
| --- | --- | --- |
| Chub OpenClaw 插件 | 已实现 | 只将可信微信私聊转发到 Chub 统一调度接口 |
| 腾讯微信插件 | 已接入 | 提供 ClawBot 账号绑定、微信消息收发和可信语音转写 |

| 能力 | 类型 | 状态 | 场景与功能 |
| --- | --- | --- | --- |
| 微信 `before_dispatch` | Hook | 已实现 | 将可信微信私聊转发到 Chub 统一调度接口 |

### 3.2 对外固定 API

本表只登记供 OpenClaw、微信或其他已批准集成方使用的固定 API；它不是工作台内部 API 或未来能力编排 Host 的完整列表。通知 API 仅接受真实 loopback 或允许的 Tailnet 来源，不是 OpenClaw 插件调用面。

| 请求 | 调用场景 | 功能 |
| --- | --- | --- |
| `GET /api/ai/usage` | 微信状态、Session 回执、任务通知、受控调用方 | 查询默认 Runtime 的受限用量快照；只返回目标 Runtime 的实际结果，未提供或读取失败时返回不可用；字段与展示口径以该 Runtime 专属设计为准 |
| `POST /api/openclaw/wechat-chub-mode/dispatch` | 微信 `before_dispatch` | 调度可信微信私聊 |
| `GET /api/notifications/targets` | 受信通知调用方 | 列出预配置飞书目标的非敏感摘要 |
| `GET /api/notifications/users?query=<姓名或用户ID>` | 受信通知调用方 | 查询候选稳定用户 ID 与展示名，不返回 Open ID |
| `POST /api/notifications/send` | 受信通知调用方 | 向预配置群目标发送纯文本、`@all` 或已登记用户；相同未知请求不会自动重发 |

## 4. 相关文档

| 文档 | 负责内容 |
| --- | --- |
| [README](../README.md) | 项目概览、安装、主要入口和文档导航 |
| [OpenClaw 定制集成设计](OPENCLAW_CUSTOMIZATION_DESIGN.md) | 微信端到端业务、身份、权限、插件定制、Context Token 和通知 |
| [Chub Session 状态模型设计](AI_SESSION_STATE_DESIGN.md) | Chub Session、Native Session 数据消费与映射、Activity、usage 投影、入口、槽位和单 writer 语义 |
| [Chub Codex Runtime 设计](CHUB_CODEX_RUNTIME_DESIGN.md) | 当前 Codex Runtime 的专属边界，以及 Codex/OpenAI 用量来源、接口、缓存和展示口径 |
| [Chub Quick Worker 独立服务设计](CHUB_QUICK_WORKER_DESIGN.md) | Quick Worker 独立服务、非实时任务、恢复、通知终态和重启协调 |
| [Chub 任务编排插件模块架构设计](CHUB_TASK_ORCHESTRATION_PLUGIN_DESIGN.md) | 任务编排插件模块的通用未来边界；不改变本节当前固定指令契约 |
| [Chub OpenClaw 插件说明](../integrations/openclaw/chub/README.md) | 插件协议、源码、构建、部署和协议验收 |
