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
| 微信 ClawBot | 已授权 Owner 通过私聊远程使用 Chub | 查询摘要、管理 Codex Session 和活动需求 |

电脑端 CLI 与微信 ClawBot 是两套独立指令：前者用于本机服务运维，后者经 OpenClaw 转发到 Chub。当前没有 npm、PyPI 或独立发行包；`chub install` 只表示从当前工作区安装本机用户服务，不表示包管理器安装。正式分发方案仅记录在状态为“待实现”的[Chub CLI 分发、安装与发布设计](CHUB_CLI_DISTRIBUTION_DESIGN.md)，不属于本节当前能力。

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
- `chub check`
- `chub worker-health`
- `chub worker-drain`
- `chub worker-reload`
- `chub worker-recover`
- `chub logs`

`chub help` 是当前 CLI 的无服务帮助入口。

`chub check` 是只读的完整系统检查入口，依次检查项目配置、用户服务、Web 健康、Quick Worker 健康和 `/api/status` 系统状态；任一必需检查失败时返回非零退出码，不执行重启、升级或任务清理。

当前三个运行部分的入口边界如下：

| 运行部分 | 当前状态 | 当前入口与职责 |
| --- | --- | --- |
| Chub Web | 已实现 | 由 `chub install/start` 管理用户服务；浏览器用于页面和快速交互 |
| Quick Worker | 已实现 | 与 Web 分离运行但由同一 CLI 安装/启动；通过 `chub worker-health` 检查，不提供普通用户独立启动入口 |
| ClawBot | 已接入 | 由 OpenClaw Gateway、微信通道和 Chub OpenClaw 插件共同提供；不由 `chub start` 启动，安装/配置以[插件说明](../integrations/openclaw/chub/README.md)为准 |

“已接入”表示 Chub 与相关通道的接口和路由已经具备，不表示本仓库包含 OpenClaw Gateway 或腾讯微信插件的源码、安装包和账号绑定流程。新设备应先按外部项目文档安装这两项，再按已生效的[插件说明](../integrations/openclaw/chub/README.md)部署 Chub 插件并完成微信验收；[Chub CLI 分发、安装与发布设计](CHUB_CLI_DISTRIBUTION_DESIGN.md)仅记录待实现的正式分发目标。

当前 `chub` 命令来自仓库内的 `scripts/chub`，依赖当前工作区、`.venv` 和本机配置。项目尚未发布 npm/PyPI/独立发行包，因此 `npm install -g chub`、`pipx install chub` 和无仓库启动不属于当前可用能力。新设备安装、npm 发布、版本管理和 GitHub Release 的目标方案见状态为“待实现”的[Chub CLI 分发、安装与发布设计](CHUB_CLI_DISTRIBUTION_DESIGN.md)，不能替代本节命令。

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

`RN` 只接受 `R1`–`R9`。标题最多 48 字符，正文最多 2000 字符；空正文、活动槽位已满或目标不存在时明确失败。保存和更新只在维护者明确要求后由本机编码 Agent 执行，不提供微信写入入口，也不得直接编辑需求状态文件。本机 CLI 不提供归档子命令；归档和删除使用第 4 节登记的微信固定指令。

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

本节是微信 Chub 固定指令的唯一产品契约。固定指令统一从文本开头解析，采用表内登记的固定格式；每条消息最多识别一条指令，暂不支持复合指令。完整匹配的表内指令由固定路由处理，匹配失败保留原文并降级为普通任务。

### 4.1 指令契约

| 英文主指令 | 中文别名 | 当前行为 |
| --- | --- | --- |
| `chub` | `状态`、`查询状态` | 只读查询 Chub、Session、活动需求和用量摘要 |
| `check` | `检查` | 只读检查当前 Chub/Web、Quick Worker 和系统状态；不执行重启、升级或任务清理 |
| `usage` | 无 | 只读查询完整额度使用情况，不附加 Session 状态 |
| `text [mode [direct\|auto\|confirm]\|list\|ok\|next\|cancel]` | 无 | 查询或调整微信后续正文处理方式；无参数时同时显示当前待确认任务，`list` 显示完整正文处理流水 |
| `help` | `帮助` | 返回不附带状态尾部的双语指令清单 |
| `model` | `模型` | 只读返回当前绑定 Session 的实际模型和推理等级；仅为发生变化的字段额外显示下一任务配置 |
| `model list` | `模型列表` | 只读返回当前绑定 Session 的下一任务模型和 Codex 可用模型列表，不返回 Session 列表或额度尾部 |
| `model level [M#]` | `模型等级 [M#]` | 只读返回下一任务模型（或当前目录中指定索引的模型）及其可用推理等级；无参数查询同时显示下一任务等级 |
| `model use M#` | `模型切换 M#` | 空闲时为当前 Session 的后续任务配置指定模型及其默认等级 |
| `model use L#` | `模型切换 L#` | 空闲时为当前 Session 的后续任务配置当前模型的指定等级 |
| `model use M# L#` | `模型切换 M# L#` | 空闲时原子配置指定模型和该模型的指定等级 |
| `text-check <English>` | 无 | 仅在收到 `Translation ready` 后以英文复述确认队头 |
| `restart` / `restart web` | `重启 Web` | 登记 Chub Web 重启并独立通知最终结果 |
| `restart worker` | `重启 Worker` | 清空排队任务、停止执行中任务并恢复 Quick Worker，独立通知最终结果 |
| `restart clawbot` | `重启 ClawBot` | 同步固定插件/补丁基线，重启 Gateway 和消息通道，独立通知最终结果 |
| `upgrade` | `升级系统` | 直接启动当前系统升级与恢复；复用页面的全部前置检查和执行器 |
| `sync` | `同步` | 扫描并原子补齐符合配置的 Session 槽位 |
| `new [title]` | `新建 [标题]` | 创建并选中新 Session；提供标题时同时命名，不提交任务 |
| `rename <title>` | `重命名 <标题>` | 重命名当前绑定 Session |
| `switch <SN> [task]` | `切换槽位 [正文]` | 选择目标 Session；有正文时切换后提交任务 |
| `S1`–`S9 [task]` | `会话槽位 [正文]` | 选择目标 Session；有正文时切换后提交任务 |
| `stop [SN]` | `停止任务` | 有槽位时停止指定 Session 中的任务；无槽位时停止当前绑定 Session 中的任务；保留 Session 槽位和历史 |
| `archive <SN>` | `归档槽位` | 仅明确已知执行状态不可归档；与 Web 共用统一归档流程，有原生 Session 时优先归档原生 Session，成功后清理 Chub 并释放槽位；无原生 Session 时直接清理；外部占用仍拒绝，状态未知交由原生归档结果决定 |
| `del <SN>` | 无 | 永久删除目标 Session 及其 Chub 记录并释放槽位；删除前执行运行态、外部占用和结果确认门禁 |
| `cat <R1-R9>` | `查看 R1-R9` | 查看活动需求的完整内容和最近状态 |
| `archive <R1-R9>` | `归档 R1-R9` | 保存归档快照并释放需求槽位 |
| `del <R1-R9>` | 无 | 永久删除活动需求，不保留归档快照 |
| `retry` | `重试`、`继续执行` | 把待续提任务提交到当前 Session |

### 4.2 语法与匹配

- 固定指令只从文本开头匹配；匹配前移除首尾空白，固定指令末尾的 Unicode 标点可忽略。没有完整匹配的内容均按普通任务发送；Unicode 符号、Emoji 和消息中段出现的指令文本不自动触发固定路由。
- 匹配失败包括缺少参数、槽位非法、附加不符合语法的正文和未知指令；这些消息不得返回 `Usage` 或执行部分指令，必须按原文作为普通任务提交。只有普通消息自身为空或全为空白时才拒绝提交。
- `<value>` 表示必填参数，`[value]` 表示可选参数。
- 会话槽位表示为 `SN = 1`–`9` = `S1`–`S9` = 中文数字“一”至“九”；需求槽位使用 `R1`–`R9`，`S`、`R` 不区分大小写。
- `S1`–`S9` 是持久槽位标识，不是 Session 列表的当前排序位置；列表排序变化不改变槽位。
- `R1`–`R9` 是最多九个活动需求的真实槽位，不是更大列表的排序别名；英文 `cat`、`run` 和需求归档使用 `R1`–`R9`，中文别名使用对应的需求槽位。
- `archive 2`、`archive2`、`archive S2` 和 `archiveS2` 归档 Session，`archive R2` 归档需求；`del 2`、`del2`、`del S2` 和 `delS2` 删除 Session，`del R2` 删除需求；需求形式优先于 Session 形式匹配。`cat README`、`run tests`等非需求槽位正文仍进入普通任务，不扩展为文件读取或系统命令。
- Session 槽位指令 `switch`、`archive` 和 `del` 与合法槽位之间允许有空格或无空格；`stop` 的槽位参数可省略，省略时使用当前绑定 Session。带槽位的 `stop` 同样允许空格或无空格。裸槽位指令使用 `S1`–`S9`，后面可直接连接正文。中文 `切换`、`会话`、`停止`、`归档` 与合法槽位之间同样允许有空格或无空格。因此 `switch2`、`switch S2`、`switchS2`、`stop`、`stop2`、`stop S2`、`停止`、`停止2`、`归档S2`、`切换二`、`切换S2` 和 `会话S2` 均按对应规则处理。需求指令 `cat/run/archive/del R...` 仍保留 `R` 前缀，以避免与 Session 槽位混淆。
- `restart`、`restart web`、`restart worker`、`restart clawbot` 和 `upgrade` 均为精确无参数指令；大小写和首尾标点可忽略，附加任何正文时回退为普通任务。`restart` 与 `restart web` 都只作用于 Web；其余三条分别只作用于 Quick Worker、ClawBot 或系统升级与恢复，不把目标名称交给客户端自由解析。
- `restart worker` 会立即登记恢复操作，取消排队任务并停止执行中任务，不自动重放；`restart clawbot` 会在当前 OpenClaw 调度请求返回后异步执行，先同步固定兼容基线，再重启 Gateway，并在最终状态确认后发送独立结果。
- `upgrade` 不接受版本号、路径或其他参数；升级方案不可用时按固定规则降级为当前版本运行态恢复，并明确不执行代码版本升级。升级受理后通过独立完成通知返回最终结果；不再提供微信 `upgrade status` 固定指令。系统升级页面和固定 API 的只读状态查询仍然保留。
- `usage` 是精确无参数指令；大小写和首尾标点可忽略，附加任何正文时回退为普通任务。
- `model` 是精确无参数指令；大小写和首尾标点可忽略，附加任何正文时回退为普通任务。它返回当前绑定 Session 最近一次已确认的 active 模型和推理等级；active 值缺失时继续读取该 Session 配置与 Runtime 模型目录默认值。若当前 Session 已为后续任务保存不同的模型或等级，只为不同字段额外显示 `Next model` 或 `Next level`，不把保存成功误报为已运行任务已切换。Runtime 目录无法读取或仍未提供某字段时才显示 `Default`，当前 Session 不存在或无法读取时失败关闭，不附加 Session 列表或额度尾部。
- `model list` 是精确无参数指令，中文别名为 `模型列表`；它显示当前 Session 为下一任务配置的模型，并以 `M1`…列出 Runtime 模型目录中当前可用的模型 ID。列表用于帮助选择，但不是后续切换的前置步骤；目录或当前模型无法确认时失败关闭，不返回残缺列表。
- `model level` 可不带参数，或携带 `M#`，中文别名为 `模型等级`。无参数时显示当前 Session 为下一任务配置的模型、当前等级及该模型的 `L1`…列表；带 `M#` 时按本次请求读取的当前 Runtime 目录选择目标模型，并只显示其 `L#` 列表而不切换。无需先执行 `model list`；索引、目录、模型、当前等级或等级列表无法确认时失败关闭。
- `model use M#`、`model use L#` 和 `model use M# L#`（中文 `模型切换`）是精确参数指令。每次请求直接读取当前 Runtime 模型目录解析索引，无需先执行 `model list` 或 `model level`：仅模型时使用目标模型声明的默认等级；仅等级时保持当前模型；二者同时提供时，`L#` 按该目标模型本次可用等级列表解析并原子保存。切换只允许当前 `quick` Session 空闲且没有 Runtime writer 或 Worker 任务时执行，只影响后续任务，不改变已经运行的任务；成功回执显示实际保存的下一任务模型和等级。目录可能在两次消息之间变化，因此回执是最终选择依据；目录、索引、兼容性、空闲状态或保存结果不能确认时失败关闭，不部分更新、不猜测且不接受原始模型或等级 ID。
- `switch`、裸 `S1`–`S9`、`切换`、`会话` 的合法槽位后如果还有内容，开头的空格及 Unicode 标点/符号作为分隔符移除，剩余内容作为正文；也允许正文直接紧连。例如 `切换S2 正文`、`会话S2，正文`、`S2正文` 等价。正文内部和结尾不改写。
- 无参数指令必须整句匹配。切换后的剩余内容始终作为普通任务正文；例如 `切换S2重试` 和 `切换S2重试服务` 都不会触发续提指令。`new retry`、`新建 重试` 和 `新建 继续执行` 也作为普通任务，不再创建 Session 或续提任务。
- 只有表内指令和别名属于固定指令。未登记的 `sn ...`、`session ...` 形式（例如 `sn S2`、`session 2`、`session switch 2`）和旧别名作为普通任务，不猜测为固定指令。
- 指令解析必须先整体判定槽位，不能把连续数字拆成合法前缀再执行；例如 `切换S10正文`、`切换10正文` 匹配失败后作为普通任务，不得误解析成 `S1` 加正文 `0正文`。
- `new` 无标题时使用创建后的默认 Session 名称；提供标题时标题不能为空且最多 48 个字符。`rename` 的标题不能为空且最多 48 个字符。`switch` 仅在槽位后存在正文时提交任务；`stop` 可省略槽位但不接受任务正文；`archive`、`del`、四条维护指令和续提指令不接受任意附带正文。
- `cat R1-R9`、`archive R1-R9` 和 `del R1-R9` 不接受附带正文；缺失、连续多位、越界或非 R 英文需求槽位时匹配失败并作为普通任务提交。中文 `查看 R1-R9` 和 `归档 R1-R9` 也指向对应需求槽位。需求标题最多 48 字符，正文最多 2000 字符。

### 4.3 Session 与任务行为

- 微信和 ClawBot 只分配 `quick` Session 的 S1–S9 槽位；`terminal` Session、升级扫描得到的 `discovered` Session 和内部翻译 Session 均不进入微信槽位或微信 Session 列表。微信不会创建或切换到实时终端 Session。
- Session 类型在创建时固定；微信 `new` 创建的是 `quick` Session，Web 创建时由弹窗选择类型。不存在把同一 Session 从实时终端切换为快速交互的复合路径。

- 设置页“正文处理方式”只影响之后新接收的正文任务：`直接执行`直接提交；`自动润色后执行`在独立只读 Session 生成中文润色和 English 后，自动提交润色后的中文；`自动润色后确认执行`生成同一份结果后进入确认队列。普通任务以及携带正文的 `switch` / `S1`–`S9` 在正文不超过 `translation_preprocess_max_input_chars`（默认 1200 字符）时遵循该快照；超过阈值直接提交原正文，不润色、不翻译、不进入确认队列，以保持同步确认回复有界。其他固定指令和续提指令仍绕过文本优化。旧布尔配置 `translation_enabled=false/true` 分别等价于 `direct/auto`。
- `text` 与设置页读取、保存同一个节点级处理方式：`text` 或 `text mode` 返回当前模式及当前已送达、可操作的待确认任务；其中 `text` 返回目标 Session、完整 `Polished` 正文和完整 `English`，不使用摘要。`text mode direct|auto|confirm` 立即保存并只影响之后新接收的正文。`text list` 显示完整正文处理流水：当前可操作确认队头以 `Confirming` 固定在最上方，其余已润色待轮到的确认项为 `Waiting confirmation`，仍在排队或翻译中的项目为 `Optimizing`；已确认但目标暂忙的项目显示为 `Waiting target`。每项返回目标 `S<槽位> · <标题>` 与下一行 `Task · <受限正文摘要>`，目标已不可用时显示 `Session · Unavailable`。已经在润色、确认或等待目标可写的项目继续按创建时快照完成。`text` 控制指令及 `text-check` 参数错误只返回用法而不作为普通任务提交。
- 确认模式下，翻译 FIFO 不等待用户操作；完成的译文按翻译完成顺序进入独立确认 FIFO。仅当队头的 `Translation ready` 通知已经成功送达时，文字消息才可使用 `text ok`（提交润色中文）、`text cancel`（丢弃）、`text next`（移至确认队尾）或 `text-check <完整英文复述>`。英文比较忽略大小写、空白和常见标点，词级相似度达到 90% 即提交润色中文；未达到时保留队头并提示重试。没有已送达的待确认队头时，这些确认指令明确回复没有待确认正文；确认、取消或过期均不会提交原文；待确认项 24 小时后失效。
- `text cancel` 与 `text next` 的同步回复先说明本次取消或后移结果，再附加更新后的 `text list` 正文处理流水；队头由此变化后，列表首项就是下一条可操作或待送达确认。
- 翻译完成后目标暂忙时，自动执行正文与已确认正文都保留固定目标，待该目标可安全写入后自动重试；自动执行视为已确认，不得改投其他 Session，也不会要求再次英文复述。
- 携带正文的切换先完成并持久化目标槽位，再启动正文的文本优化；同步回执显示 `Optimizing · Preparing to submit.`，表示正文已进入受控准备流程但尚未由主任务接收。文本优化任务被可靠受理后，普通任务的插件以 `handled` 静默结束同步链路；切换任务保留已切换和优化中的回执。预处理提交不受目标 Session 当前运行态或已有翻译项阻塞，可继续进入翻译 FIFO；直接执行仍按当前 Session 忙碌规则拒绝。初始校验或翻译任务受理失败时仍即时回复错误。润色成功且主任务被 Quick Worker 接收后发送 `Started`；润色失败、润色或 English 任一部分超过 8000 字符、原目标失效时均通知失败，不执行原文、不生成待续提任务；目标暂忙则按固定目标等待重试。带正文切换已成功时保留切换结果，但正文不会提交。跨重启的持久化与幂等收敛由接入设计和 Worker 设计维护。
- 同一目标 Session 可以继续进入翻译 FIFO 和确认 FIFO；翻译完成后的主任务写入仍由该固定目标的真实执行状态仲裁。目标正忙时，自动执行与已确认任务保留原目标并按恢复规则重试，不改投其他 Session；不同目标的主任务可并行。
- 普通提交路径中，当前 Session 忙时拒绝提交，并短期保存最近一次待续提正文。
- 普通任务、切换后提交和续提任务成功后，回执必须列出全部已登记 Session；每个运行中的 Session 紧跟对应 `Task`，当前绑定继续使用 `▶` 标记，方便直接判断可切换槽位。列表采集失败不得把已成功提交误报为失败，至少保留本次可信任务上下文。
- `new [title]` 创建成功后即选中新 Session；无标题时保留创建后的默认名称，提供标题时再执行命名；当微信 Chub 模式未显式配置模型和推理等级时，使用设置页保存的当前节点新建默认，命名失败时保留该 Session，并提示使用 `rename` 修正。显式微信配置优先于节点默认，已有 Session 不受新默认变化影响。
- `stop [SN]` 先回复已安排，再异步取消目标 Session 中的任务并停止其当前执行载体；省略槽位时目标为当前绑定 Session。只有目标由 Chub 终端或 Quick Worker 执行时才允许，原生 Session 被外部进程占用或当前没有执行时拒绝。最终结果只发送到本次保存的微信路由。停止不释放槽位，只有归档或删除操作释放槽位。
- `archive SN` 仅在明确知道 Session 正处于执行中时拒绝，并与 Web 使用同一条归档流程；状态未知时先交给 native session 尝试归档，由 native 返回可行性和最终结果。有原生 Session 时，原生失败或结果未知则保留 Chub 记录、任务和槽位并返回原因；原生归档成功后再清理记录并释放槽位。没有原生 Session 时直接完成 Chub 侧清理。若原生归档已完成但 Chub 清理或槽位同步中断，保留记录并允许重试，重试先确认原生已归档，不重复执行原生归档。
- `del SN` 永久删除目标 Session 及其 Chub 记录；删除前先取消可取消的 Quick Worker 任务并关闭 Chub 终端载体，再由 Runtime 确认原生删除，成功后释放槽位。外部占用、运行态或结果未知时失败关闭并保留槽位；删除结果未知时不得宣告成功。
- 重复消息不重复执行。重复回执发送前刷新当前标记；槽位已释放或复用时移除不再可信的 Session 行。
- 插件等待 Chub 超时时只说明提交状态未知，不生成 Session、任务摘要或成功结论。

需求储备行为：

- 需求讨论继续使用普通 Session，不创建隐藏或专用需求 Session。微信不提供保存、更新指令；维护者明确要求后，由当前编码 Agent 按 `AGENTS.md` 使用本机受控入口保存或整体更新，不能直接编辑状态文件，也不能因普通讨论自动保存或开始实现。
- 新需求占用编号最小的空槽位，最多九个活动需求；更新不改变槽位。`chub`只在自身状态摘要中列出活动需求标题，其他完整 Session 回执不追加 Requests。无活动需求时显示单行`No requests`，读取失败显示`Requests`和`Unavailable`，不得误报为空。
- `check` / `检查` 只读取当前 Web 进程的 Chub 就绪状态、Quick Worker 私有健康状态和系统内存/磁盘指标，返回 `Check · <耗时>`、`【服务】`、`【资源】` 和 `【结果】` 四段固定摘要；它不附加 Session、任务上下文或额度尾部，不调用任意命令或路径，不检查并修改服务管理器状态，不重启服务、不升级系统、不清理任务。输出不得包含 PID、generation、主机路径、凭证或其他敏感信息。
- `cat R1-R9` 完整返回标题、`Ready` 状态和正文，不附加 Session/用量尾部。
- `archive R1-R9` 保存归档快照并释放槽位；活动槽位随后可被新需求复用，旧需求槽位不再指向归档内容。当前最多保留最近 100 个归档快照，首版不提供归档查询、恢复、搜索或排序指令。
- `del R1-R9` 直接删除活动需求并释放需求槽位，不创建归档快照；删除后该槽位可被新需求复用。

### 4.4 回复格式

| 规则 | 契约 |
| --- | --- |
| 固定文案 | 默认使用英文；任务标题保留来源原文，Session 名称按任务保存的 `session_id` 在展示时读取当前值；`help` 是双语清单例外 |
| 帮助清单 | 按“查询与帮助指令 / 会话指令 / 需求指令 / 系统维护指令”分组，展示当前对外公开的指令与参数；标题与每项均为独立段落 |
| Session 行 | `[▶ ]S<槽位>[ !] · <标题>`；`▶` 仅表示当前绑定，`!` 表示不可用或状态未知 |
| Task 行 | `Task · <摘要>`；无可信摘要时使用 `Task · Running`；无 Task 行表示没有运行任务 |
| Request 行 | `R<槽位> · <标题>`用于`chub`列表和需求查询结果 |
| 成功任务回执 | `状态`、`Sessions`、全部已登记 Session 及各自运行 Task；可用 Session 不显示 Task |
| 失败任务回执 | `状态`、可信目标 Session、Task 依次使用独立段落；无法确认目标时省略 Session |
| 段落 | Session、Task、结果正文和帮助项不得用可能被电脑微信折叠的单换行分隔 |
| 空状态 | 无 Session 时显示`No sessions`；无活动需求时显示`No requests`；读取失败显示对应`Unavailable`或`Weekly Unavailable` |
| 列表截断 | 未展示数量使用 `<N> more Sessions` |
| `check` 回执 | 返回 `Check · <耗时>`，按“服务 / 资源 / 结果”分段展示脱敏核心检查明细；不附加任务、Session 或用量上下文 |

Session 标题与任务摘要的显示规则：

- `session_name_max_width` 默认 30，`task_name_max_width` 默认 64；两项允许范围均为 4–96。
- 半角字符按宽度 1，汉字与全角字符按宽度 2；Emoji 和组合字符保持完整字形。
- 超出显示宽度时预留 `…`；原始 Session 标题和任务摘要另有 48 字符安全上限。

### 4.5 状态尾部与通知

| 回复类型 | Session/用量状态尾部 |
| --- | --- |
| `help`、`Usage` 用法错误 | 不附加 |
| `usage` | 只返回完整额度使用情况，不附加 Session 状态 |
| `model`、`model list`、`model level`、`model use` | 只返回模型查询或配置结果，不附加 Session 列表或用量状态 |
| `check` | 使用任务完成格式，但不提交 AI 任务；只附加本次 `Weekly` 尾部，不附加完整 Session/用量状态 |
| `cat <R1-R9>`、`archive <R1-R9>`及其失败 | 不附加 |
| `del <SN>`、`del <R1-R9>`及其失败 | 不附加 |
| `stop [SN]` 的首次受理或进行中回复 | 不附加 |
| 四条维护指令的首次 `Scheduled` 回复 | 只返回 Scheduled，不附加 Session/Task 状态和用量 |
| `restart web` 已在进行中或同步失败的回复 | 按现有固定指令规则附加可用状态 |
| `upgrade` | 不附加 Session/用量状态；直接返回升级受理结果，最终状态通过独立通知返回 |
| 切换并提交任务、续提任务、切换后正文的优化中回执或未启用文本优化的普通任务回执 | 不附加 |
| 其他固定指令结果 | 附加 Session 状态和 `Weekly <quota> · Today <usage>` |
| 主任务成功通知 | 只在结果底部追加用量，不附加完整 Session 状态 |
| 主任务失败、超时及文本优化通知 | 不附加 |
| Web、Worker、ClawBot 重启的独立完成通知 | 附加操作后的最终状态；受理或进程启动不视为完成 |

`usage` 的完整回执使用独立分行格式：`Weekly` 显示带美元符号的剩余金额及其末尾的 `Remaining` 标签、周额度剩余百分比及其末尾的 `left` 标签；`Today` 显示带美元符号的已用额度及其末尾的 `Used` 标签和 Token，`Resets` 使用 `YYYY-MM-DD HH:MM` 的本地时间格式。微信回执暂不显示限额数值。首页额度长格式不受此指令调整影响。

- 尾部读取失败只降级对应状态，不得覆盖指令本身的成功或失败语义。
- 异步任务和文本优化队列只保存目标 `session_id`；`Started`、完成和失败通知发送时按该 ID 读取当前槽位与 Session 名称。润色任务的 `Started` 使用 `Started`、发送时校验的 `[▶ ]S<槽位> · <标题>`、`Submitted:` 完整润色中文及 `English:`；确认模式在确认结果持久化后立即结束微信入口请求，主任务提交与这一条 `Started` 均由确认队列异步处理，不再额外发送 `Translation confirmed · Preparing to submit.`，也不把 Worker 或通知耗时误报为提交未知。槽位暂忙时先回复等待，待实际接收后再发送 `Started`。槽位已释放或复用时标记 `Unavailable`，不得把新 Session 显示成原任务目标。
- 主任务终态继续使用 `Done`、`Failed` 或 `Timed out`，`Task · <摘要>` 必须来源于实际提交文本。正常成功链路通常产生 `Started` 和 `Done` 两次异步通知；两者不设置到达顺序门禁，极快任务允许偶发轻微乱序。
- 失败任务页面时间线明确显示错误来源：`Chub` 表示 Chub/Worker/解析边界错误，`Codex CLI（上游 Runtime）` 表示当前 Codex Runtime 子进程提供的原始诊断。微信完成通知只在 `Failed` 或文本优化失败标题中追加 `Chub` 或 `Codex CLI (upstream Runtime)`；`Timed out` 和 `Cancelled` 不追加错误来源。错误正文仍按 Worker 固定上限脱敏并以纯文本发送。`error_source=runtime` 保持 Runtime 通用语义，未来接入其他 Runtime 时必须重新定义对应展示标签并同步本节。
- 文本优化失败或目标不可提交时只发送对应的一次异步失败通知。确认提示使用 `Translation ready`，包含固定目标、`Polished:`、`English:`，底部仅提示 `Please confirm.`；可用确认指令仍以本节命令契约为准。提示送达失败时不开放确认命令并持续按恢复规则重试。所有通知继续执行固定分段和总条数上限，超长内容可能拆成多条物理消息。
- 普通任务结果通知失败保留在后台任务状态和运行日志中，不在 `chub` 中长期展示。
- 重启、停止结果通知失败会影响维护操作终态判断，继续在 `chub` 的 Issues 中展示。

### 4.6 调整指令时的同步清单

新增、删除或修改固定指令时，必须同时完成：

1. 更新本节的指令表、语法边界、业务行为和回复规则。
2. 同步 Chub 指令解析、双语 `help`、成功/失败业务测试及普通任务回退测试。
3. 检查 [OpenClaw 定制集成设计](OPENCLAW_CUSTOMIZATION_DESIGN.md)是否受到影响；只有身份、路由、并发、持久化或通知边界变化时才更新设计正文，不复制本节的指令表和格式规则。
4. 验证英文与中文别名、`SN = 1-9 = S1-S9 = 一…九`、`R1-R9` 需求槽位、中英文空格边界、最长指令优先、幂等重放、段落换行和状态尾部。
5. 涉及调度协议、插件配置或交付决定时，再按 [Chub OpenClaw 插件说明](../integrations/openclaw/chub/README.md)完成协议升级清单、构建和部署验证。

身份、权限、并发、持久化、路由和通知安全边界以 [OpenClaw 定制集成设计](OPENCLAW_CUSTOMIZATION_DESIGN.md)为准。

## 5. 相关文档

| 文档 | 负责内容 |
| --- | --- |
| [README](../README.md) | 项目概览、安装、主要入口和文档导航 |
| [OpenClaw 定制集成设计](OPENCLAW_CUSTOMIZATION_DESIGN.md) | 微信端到端业务、身份、权限、插件定制、Context Token 和通知 |
| [Chub AI Session 状态模型设计](AI_SESSION_STATE_DESIGN.md) | Session、Activity、usage 投影、入口、槽位和单 writer 语义 |
| [Codex AI 额度与用量采集设计](CODEX_AI_QUOTA_USAGE_DESIGN.md) | Codex/OpenAI 用量来源、统一接口、缓存和展示口径 |
| [Chub Quick Worker 独立服务设计](CHUB_QUICK_WORKER_DESIGN.md) | Quick Worker 独立服务、非实时任务、恢复、通知终态和重启协调 |
| [Chub CLI 分发、安装与发布设计](CHUB_CLI_DISTRIBUTION_DESIGN.md) | 新设备安装、核心 Chub 与可选 ClawBot 职责、npm 发布、版本管理和 GitHub Release |
| [Chub OpenClaw 插件说明](../integrations/openclaw/chub/README.md) | 插件协议、源码、构建、部署和协议验收 |
