# Chub 集成能力清单

> 状态：持续维护。
> 主要读者：AI Agent、实现和排障 Agent；维护人员用于确认当前可调用能力、指令契约和同步清单。
> 本文负责：在[Chub 总体架构](CHUB_ARCHITECTURE_DESIGN.md)之后统一登记“当前能调用什么”；第 4 节是微信 Chub 固定指令的唯一产品契约。
> 本文不负责：实现细节、身份安全、并发/持久化/调度协议字段或尚未实现的目标架构；这些内容由对应专项设计和插件 README 维护。

微信固定指令的完整语法、用户可见行为和回复格式以第 4 节为准；身份、安全、并发、持久化和通知路由见对应设计文档；项目整体功能与使用方式见 [README](../README.md)。

## 1. 使用入口

| 入口 | 使用场景 | 主要能力 |
| --- | --- | --- |
| 电脑端 `chub` CLI | 在 Chub 所在电脑安装、维护和排查服务 | 管理 Web 与 Quick Worker、维护需求储备、查看日志、发送预配置通知 |
| OpenClaw Agent Tool | OpenClaw TUI 或未进入微信 Chub 模式的 Agent 调用 | 查询 Chub 基础状态、发送预配置飞书通知 |
| 微信 ClawBot | 已授权 Owner 通过私聊远程使用 Chub | 查询摘要、管理 Codex Session 和活动需求、提交任务并接收结果 |

电脑端 CLI 与微信 ClawBot 是两套独立指令：前者用于本机服务运维，后者经 OpenClaw 转发到 Chub。包管理器（例如 npm 或 PyPI）只负责未来的版本分发，不属于当前 CLI 能力；`chub install` 仍表示本机用户服务安装，不表示包管理器安装。分发目标和职责边界见 [Chub CLI 分发、安装与发布设计](CHUB_CLI_DISTRIBUTION_DESIGN.md)。

## 2. 电脑端命令

### 2.1 Chub CLI

- `chub`
- `chub help`、`chub -h`、`chub --help`
- `chub install [--force]`
- `chub uninstall [--force]`
- `chub start`
- `chub stop [--force]`
- `chub restart`
- `chub status`
- `chub worker-health`
- `chub worker-drain`
- `chub worker-reload`
- `chub logs`

`chub help` 是当前 CLI 的无服务帮助入口；未来版本必须在帮助首部给出新设备的 `npm install -g chub`、`chub help`、`chub start` 流程，并提示 Web/Quick Worker 与可选 ClawBot 的职责差异。

当前三个运行部分的入口边界如下：

| 运行部分 | 当前状态 | 当前入口与职责 |
| --- | --- | --- |
| Chub Web | 已实现 | 由 `chub install/start` 管理用户服务；浏览器用于页面和快速交互 |
| Quick Worker | 已实现 | 与 Web 分离运行但由同一 CLI 安装/启动；通过 `chub worker-health` 检查，不提供普通用户独立启动入口 |
| ClawBot | 已接入 | 由 OpenClaw Gateway、微信通道和 Chub OpenClaw 插件共同提供；不由 `chub start` 启动，安装/配置以[插件说明](../integrations/openclaw/chub/README.md)为准 |

“已接入”表示 Chub 与相关通道的接口和路由已经具备，不表示本仓库包含 OpenClaw Gateway 或腾讯微信插件的源码、安装包和账号绑定流程。新设备应先按外部项目文档安装这两项，再按[Chub CLI 分发、安装与发布设计](CHUB_CLI_DISTRIBUTION_DESIGN.md)部署 Chub 插件和完成微信验收。

当前 `chub` 命令来自仓库内的 `scripts/chub`，依赖当前工作区、`.venv` 和本机配置。项目尚未发布 npm/PyPI/独立发行包，因此 `npm install -g chub`、`pipx install chub` 和无仓库启动不属于当前可用能力。未来分发层必须继续复用本节命令语义，不得创建第二套 CLI。新设备安装、npm 发布、版本管理和 GitHub Release 目标见 [Chub CLI 分发、安装与发布设计](CHUB_CLI_DISTRIBUTION_DESIGN.md)。

### 2.2 通知指令

- `chub notification validate`
- `chub notification list`
- `chub notification test --target <target>`
- `chub notification send --target <target> --message <message>`
- `chub notification send --target <target> --message <message> --mention-all`
- `chub notification send --target <target> --message <message> --mention-recipient <recipient> [--mention-recipient <recipient> ...]`

### 2.3 需求储备指令

- `chub request save --title <标题>`：从标准输入保存一条新需求，占用编号最小的空闲 R 槽位。
- `chub request update RN [--title <标题>]`：从标准输入整体替换活动需求正文，可同时更新标题。
- `chub request show RN`：查看一条活动需求的标题和完整正文。
- `chub request list`：按槽位列出全部活动需求。

`RN` 只接受 `R1`–`R9`。标题最多 48 字符，正文最多 2000 字符；空正文、活动槽位已满、目标不存在或需求正在运行时明确失败。保存和更新只在维护者明确要求后由本机编码 Agent 执行，不提供微信写入入口，也不得直接编辑需求状态文件。本机 CLI 不提供归档子命令；归档使用第 4 节登记的微信固定指令。

完整安装条件、平台差异和命令说明见 [README](../README.md)。

## 3. 插件与固定 API

### 3.1 插件

| 插件 | 状态 | 功能 |
| --- | --- | --- |
| Chub OpenClaw 插件 | 已实现 | 提供受限 Chub Tool、微信消息转发和飞书通知原文保护 |
| 腾讯微信插件 | 已接入 | 提供 ClawBot 账号绑定、微信消息收发和可信语音转写 |

### 3.2 Chub OpenClaw 插件能力

| 能力 | 类型 | 状态 | 场景与功能 |
| --- | --- | --- | --- |
| `chub_get_status` | Agent Tool | 已实现 | OpenClaw Agent 查询 Gateway 所在节点的 Chub 基础状态 |
| `chub_send_notification` | Agent Tool | 已实现 | OpenClaw Agent 向 Chub 预配置的飞书目标发送消息 |
| 微信 `before_dispatch` | Hook | 已实现 | 将可信微信私聊转发到 Chub 统一调度接口 |
| 飞书原文保护 | Hook | 已实现 | 按 `runId` 关联可信原文，约束通知内容来源 |

### 3.3 固定 API

| 请求 | 调用场景 | 功能 |
| --- | --- | --- |
| `GET /api/status` | `chub_get_status` | 查询节点健康和基础状态 |
| `GET /api/ai/usage` | Chub 首页、受控调用方 | 查询统一 AI 周额度、今日用量和重置时间；账号当天桶延迟时返回明确标记的本机 Token |
| `POST /api/notifications/send` | `chub_send_notification` | 向预配置目标发送通知 |
| `POST /api/openclaw/wechat-chub-mode/dispatch` | 微信 `before_dispatch` | 调度可信微信私聊 |

## 4. 微信 ClawBot 指令

本节是微信 Chub 固定指令的唯一产品契约。只有表内形式和本节明确规定的 `Usage` 错误可由固定路由处理；其他消息必须进入普通任务流程，不得猜测为固定指令。

### 4.1 指令契约

| 英文主指令 | 中文别名 | 当前行为 |
| --- | --- | --- |
| `chub` | `状态`、`查询状态` | 只读查询 Chub、Session、活动需求和用量摘要 |
| `help` | `帮助` | 返回不附带状态尾部的双语指令清单 |
| `restart` | `重启`、`重新启动` | 登记 Chub Web 重启并独立通知最终结果 |
| `system upgrade status` | 无 | 只读查询当前系统升级与恢复方案、前置条件和执行状态 |
| `system upgrade` | 无 | 直接启动当前系统升级与恢复；复用页面的全部前置检查和执行器 |
| `sync` | `同步` | 扫描并原子补齐符合配置的 Session 槽位 |
| `direct <task>` | `直接执行 <正文>` | 跳过文本优化，将原始正文提交到当前 Session |
| `new <title>` | `新建 <标题>` | 创建、命名并选中新 Session，不提交任务 |
| `rename <title>` | `重命名 <标题>` | 重命名当前绑定 Session |
| `switch N [<task>]` | `切换 <槽位> [正文]`、`会话 <槽位> [正文]` | 选择目标 Session；有正文时切换后提交任务 |
| `stop N` | `停止 <槽位>` | 异步取消目标任务并停止 Session，保留槽位和历史 |
| `archive N` | `归档 <槽位>` | 归档目标 Session 并释放槽位 |
| `cat RN` | `查看需求 N` | 查看活动需求的完整内容和最近状态 |
| `run RN` | `执行需求 N` | 把活动需求提交到当前 Session |
| `archive RN` | `归档需求 N` | 归档已停止运行的需求并释放 R 槽位 |
| `retry` | `重试`、`继续执行` | 把待续提任务提交到当前 Session |
| `new retry` | `新建 重试`、`新建 继续执行` | 新建 Session 后提交待续提任务 |
| `switch N retry` | `切换<槽位>重试`、`会话<槽位>重试`（分隔可选） | 先选择目标 Session，再提交待续提任务 |

### 4.2 语法与匹配

- `<value>` 表示必填参数，`[value]` 表示可选参数。
- 槽位参数 `N`、`SN` 与中文数字“一”至“九”等价。`N` 是半角数字 `1`–`9`，`S` 不区分大小写；`2`、`S2`、`s2` 和“二”指向同一槽位。
- `S1`–`S9` 是持久槽位标识，不是 Session 列表的当前排序位置；列表排序变化不改变槽位。
- `R1`–`R9` 是最多九个活动需求的真实槽位，不是更大列表的排序别名。英文 `cat`、`run` 和需求归档必须显式使用 `RN`；中文别名中的 `N`、`RN`与单字中文数字“一”至“九”等价，例如`查看需求 2`、`查看需求 R2`和`查看需求二`。
- `archive 2`、`archive S2`归档 Session，`archive R2`归档需求；需求形式优先于 Session 形式匹配。`cat README`、`run tests`等非 R 槽位正文仍进入普通任务，不扩展为文件读取或系统命令。
- 英文 `switch` 与槽位之间必须有空格；中文 `切换`、`会话` 与合法槽位之间允许有空格或无空格。因此 `切换二`、`切换 二`、`切换2`、`切换 S2`、`切换S2`、`会话二` 和 `会话S2` 等价。其他英文指令及槽位指令继续使用登记的空格形式。
- `system upgrade status` 与 `system upgrade` 均为精确无参数指令；大小写和首尾标点可忽略，附加任何正文时回退为普通任务。`system upgrade` 不创建 Quick Worker 任务或绑定 Session；同一升级已在进行时只返回当前进行中状态，不重复启动。
- `switch`、`切换`、`会话` 的合法槽位后如果还有内容，开头的空格及 Unicode 标点/符号作为分隔符移除，剩余内容作为正文；也允许正文直接紧连。例如 `切换S2 正文`、`切换S2，正文`、`切换S2正文` 等价。正文内部和结尾不改写。
- 无参数指令必须整句匹配。切换后的剩余内容在清理分隔符后，只有整句等于 `retry` 或 `重试` 才作为第二条指令按“先切换、后重试”顺序执行；`切换S2重试服务` 仍把“重试服务”作为普通任务正文。`new retry` 继续按最长指令优先。
- 只有表内指令和别名属于固定指令。旧 `session ...` 前缀和旧别名作为普通任务，不猜测为固定指令。
- 仅由已登记的槽位指令词和错误槽位组成时，视为明确的指令尝试：缺失参数、非单字中文数字或槽位超出 `1`–`9` 均只回复 `Usage`，不得提交普通任务或执行操作。连续数字必须先整体判定槽位，因此 `切换S10正文`、`切换10正文` 不能误解析成 `S1` 加正文 `0正文`。
- `new`、`rename` 的标题不能为空且最多 48 个字符。`direct` 的正文不能为空；`switch` 仅在槽位后存在正文时提交任务；`stop`、`archive`、`restart` 和续提指令不接受任意附带正文。
- `cat RN`、`run RN`和`archive RN`不接受附带正文；缺失、连续多位、越界或非 R 英文需求槽位只返回对应`Usage`。需求标题最多 48 字符，正文最多 2000 字符。

### 4.3 Session 与任务行为

- “自动润色后执行”关闭时，普通文字或可信语音任务直接提交到当前绑定 Session。开启时，普通任务先在独立只读 Session 生成中文润色和 English，再把润色后的中文自动提交到接收消息时锁定的目标 Session；所有固定指令（包括携带正文的 `switch` 和续提指令）均绕过文本优化。
- 文本优化任务被可靠受理后，插件以 `handled` 静默结束同步链路，不发送“处理中”回执。初始校验或翻译任务受理失败时仍即时回复错误。润色成功且主任务被 Quick Worker 接收后发送 `Started`；润色失败、润色或 English 任一部分超过 8000 字符、原目标失效或变忙时均通知失败，不执行原文、不切换 Session，也不生成待续提任务。跨重启的持久化与幂等收敛由接入设计和 Worker 设计维护。
- 同一目标 Session 已有文本优化任务时，新的任务直接拒绝；不同 Session 可并行。`direct <task>` / `直接执行 <正文>` 只跳过本次文本优化，不修改节点级开关。
- 直接提交路径中，当前 Session 忙时拒绝提交，并短期保存最近一次待续提正文。
- 普通任务、切换后提交和续提任务成功后，回执必须列出全部已登记 Session；每个运行中的 Session 紧跟对应 `Task`，当前绑定继续使用 `▶` 标记，方便直接判断可切换槽位。列表采集失败不得把已成功提交误报为失败，至少保留本次可信任务上下文。
- `new <title>` 创建成功后即选中新 Session；后续命名失败时保留该 Session，并提示使用 `rename` 修正。
- `switch N retry` 严格按两步执行：切换失败时不执行重试；切换成功后即保留目标绑定，再执行 `retry`。因此目标 Session 忙时仍先完成切换，随后重试失败并保留待续提任务；目标原本就是当前 Session 时直接继续重试。服务中断后的续跑和去重属于接入设计维护的幂等边界。
- `stop N` 先回复已安排，再异步取消任务和停止 Session；最终结果只发送到本次保存的微信路由。停止不释放槽位，只有 `archive N` 释放槽位。
- 重复消息不重复执行。重复回执发送前刷新当前标记；槽位已释放或复用时移除不再可信的 Session 行。
- 插件等待 Chub 超时时只说明提交状态未知，不生成 Session、任务摘要或成功结论。

需求储备行为：

- 需求讨论继续使用普通 Session，不创建隐藏或专用需求 Session。微信不提供保存、更新指令；维护者明确要求后，由当前编码 Agent 按 `AGENTS.md` 使用本机受控入口保存或整体更新，不能直接编辑状态文件，也不能因普通讨论自动保存或开始实现。
- 新需求占用编号最小的空槽位，最多九个活动需求；更新不改变槽位。`chub`只在自身状态摘要中列出活动需求标题，其他完整 Session 回执不追加 Requests。无活动需求时显示单行`No requests`，读取失败显示`Requests`和`Unavailable`，不得误报为空。
- `cat RN`完整返回标题、`Ready`/`Running`/`Done`/`Failed`状态和正文，不附加 Session/用量尾部。
- `run RN`先锁定本次需求版本，再把标题和正文原样提交到当前 Session。它是固定指令，绕过文本优化；当前 Session 忙或明确未提交时不生成待续提，需求保留为失败状态。消息重放不得重复提交，同一需求运行中拒绝再次执行。
- Quick Worker 已接收或提交结果暂时无法确认时，同步回执使用`Submission pending confirmation`，需求保持`Running`并等待真实任务终态，不得显示`Not submitted`、先标记失败或允许归档。成功、失败、超时或取消只更新最近状态，不自动归档。完成通知附加本次`Request · RN · <标题>`；槽位已归档复用时标记`Unavailable`。服务重启后按持久任务标识恢复状态；尚未记录任务标识时允许短暂恢复宽限，并必须在宽限到期后自动再次收敛，不猜测成功或重复执行。
- `archive RN`只允许非运行状态，保存归档快照并释放槽位；活动槽位随后可被新需求复用，旧 RN 不再指向归档内容。当前最多保留最近 100 个归档快照，首版不提供归档查询、恢复、搜索或排序指令。

### 4.4 回复格式

| 规则 | 契约 |
| --- | --- |
| 固定文案 | 默认使用英文；Session 标题和任务标题保留来源原文；`help` 是双语清单例外 |
| 帮助清单 | 按“英文主指令 · 中文别名”展示全部登记项和参数；标题与每项均为独立段落 |
| Session 行 | `[▶ ]S<槽位>[ !] · <标题>`；`▶` 仅表示当前绑定，`!` 表示不可用或状态未知 |
| Task 行 | `Task · <摘要>`；无可信摘要时使用 `Task · Running`；无 Task 行表示没有运行任务 |
| Request 行 | `R<槽位> · <标题>`用于`chub`列表；执行回执和终态通知使用`Request · R<槽位> · <标题>` |
| 成功任务回执 | `状态`、`Sessions`、全部已登记 Session 及各自运行 Task；可用 Session 不显示 Task |
| 失败任务回执 | `状态`、可信目标 Session、Task 依次使用独立段落；无法确认目标时省略 Session |
| 段落 | Session、Task、结果正文和帮助项不得用可能被电脑微信折叠的单换行分隔 |
| 空状态 | 无 Session 时显示`No sessions`；无活动需求时显示`No requests`；读取失败显示对应`Unavailable`或`Weekly Unavailable` |
| 列表截断 | 未展示数量使用 `<N> more Sessions` |

Session 标题与任务摘要的显示规则：

- `session_name_max_width` 默认 30，`task_name_max_width` 默认 64；两项允许范围均为 4–96。
- 半角字符按宽度 1，汉字与全角字符按宽度 2；Emoji 和组合字符保持完整字形。
- 超出显示宽度时预留 `…`；原始 Session 标题和任务摘要另有 48 字符安全上限。

### 4.5 状态尾部与通知

| 回复类型 | Session/用量状态尾部 |
| --- | --- |
| `help`、`Usage` 用法错误 | 不附加 |
| `cat RN`、`archive RN`及其失败 | 不附加 |
| `run RN` | 成功回执自身包含完整 Session/Task；失败不附加 |
| `stop N` 的首次受理或进行中回复 | 不附加 |
| `restart` 的首次受理或进行中回复 | 附加完整 Session/Task 状态和用量 |
| `system upgrade status`、`system upgrade` | 不附加 Session/用量状态；直接返回升级状态或启动结果 |
| 切换并提交任务、续提任务或未启用文本优化的普通任务回执 | 不附加 |
| 其他固定指令结果 | 附加 Session 状态和 `Weekly <quota> · Today <usage>` |
| 主任务成功通知 | 只在结果底部追加用量，不附加完整 Session 状态 |
| 主任务失败、超时及文本优化通知 | 不附加 |
| 重启、停止的独立完成通知 | 附加操作后的最终状态 |

- 尾部读取失败只降级对应状态，不得覆盖指令本身的成功或失败语义。
- `Started` 使用 `Started`、发送时校验的 `[▶ ]S<槽位> · <标题>`、`Submitted:` 完整实际提交文本和 `English:`；不再使用“润色后”标签。槽位已释放或复用时标记 `Unavailable`，不得把新 Session 显示成原任务目标。
- 主任务终态继续使用 `Done`、`Failed` 或 `Timed out`，`Task · <摘要>` 必须来源于实际提交文本。正常成功链路通常产生 `Started` 和 `Done` 两次异步通知；两者不设置到达顺序门禁，极快任务允许偶发轻微乱序。
- 失败任务页面时间线明确显示错误来源：`Chub` 表示 Chub/Worker/解析边界错误，`Codex CLI（上游 Runtime）` 表示当前 Codex Runtime 子进程提供的原始诊断。微信完成通知只在 `Failed` 或文本优化失败标题中追加 `Chub` 或 `Codex CLI (upstream Runtime)`；`Timed out` 和 `Cancelled` 不追加错误来源。错误正文仍按 Worker 固定上限脱敏并以纯文本发送。`error_source=runtime` 保持 Runtime 通用语义，未来接入其他 Runtime 时必须重新定义对应展示标签并同步本节。
- 文本优化失败或目标不可提交时只发送对应的一次异步失败通知。所有通知继续执行固定分段和总条数上限，超长内容可能拆成多条物理消息。
- 普通任务结果通知失败保留在后台任务状态和运行日志中，不在 `chub` 中长期展示。
- 重启、停止结果通知失败会影响维护操作终态判断，继续在 `chub` 的 Issues 中展示。

### 4.6 调整指令时的同步清单

新增、删除或修改固定指令时，必须同时完成：

1. 更新本节的指令表、语法边界、业务行为和回复规则。
2. 同步 Chub 指令解析、双语 `help`、成功/失败业务测试及普通任务回退测试。
3. 检查 [OpenClaw 定制集成设计](OPENCLAW_CUSTOMIZATION_DESIGN.md)是否受到影响；只有身份、路由、并发、持久化或通知边界变化时才更新设计正文，不复制本节的指令表和格式规则。
4. 验证英文与中文别名、`N = SN = 一…九`、中英文空格边界、最长指令优先、幂等重放、段落换行和状态尾部。
5. 涉及调度协议、插件配置或交付决定时，再按 [Chub OpenClaw 插件说明](../integrations/openclaw/chub/README.md)完成协议升级清单、构建和部署验证。

身份、权限、并发、持久化、路由和通知安全边界以 [OpenClaw 定制集成设计](OPENCLAW_CUSTOMIZATION_DESIGN.md)为准。

## 5. 相关文档

| 文档 | 负责内容 |
| --- | --- |
| [README](../README.md) | 项目概览、安装、主要入口和文档导航 |
| [OpenClaw 定制集成设计](OPENCLAW_CUSTOMIZATION_DESIGN.md) | 微信端到端业务、身份、权限、插件定制、Context Token 和通知 |
| [Chub AI Session 状态模型设计](AI_SESSION_STATE_DESIGN.md) | Session、Activity、入口、槽位和单 writer 语义 |
| [Codex AI 额度与用量采集设计](CODEX_AI_QUOTA_USAGE_DESIGN.md) | Codex/OpenAI 用量来源、统一接口、缓存和展示口径 |
| [Chub Quick Worker 独立服务设计](CHUB_QUICK_WORKER_DESIGN.md) | Quick Worker 独立服务、非实时任务、恢复、通知终态和重启协调 |
| [Chub CLI 分发、安装与发布设计](CHUB_CLI_DISTRIBUTION_DESIGN.md) | 新设备安装、核心 Chub 与可选 ClawBot 职责、npm 发布、版本管理和 GitHub Release |
| [Chub OpenClaw 插件说明](../integrations/openclaw/chub/README.md) | 插件协议、源码、构建、部署和协议验收 |
