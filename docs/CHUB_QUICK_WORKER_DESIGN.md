# Chub Quick Worker 独立服务设计

> 状态：已验收。
> 主要读者：AI Agent、实现和排障 Agent；维护人员用于确认运行边界和验收结果。
> 本文负责：Chub Quick Worker 独立服务的职责、任务权威状态、Session 租约、恢复、通知终态和重启语义，遵循[Chub 总体架构](CHUB_ARCHITECTURE_DESIGN.md)。
> 本文不负责：长期 Runtime 无关边界（见[Chub AI Runtime 架构设计](CHUB_AI_RUNTIME_DESIGN.md)）、Session/Activity 枚举与页面语义（见[AI Session 状态模型](AI_SESSION_STATE_DESIGN.md)）以及微信路由与收件人身份（见[OpenClaw 定制集成设计](OPENCLAW_CUSTOMIZATION_DESIGN.md)）。
> 维护说明：独立服务、跨 Web 重启恢复和 Runtime 通用化基线已完成当前范围验收；协议或状态边界变化时按本文末尾复检规则重新验收。

## 0. AI Agent 快速理解

把本文当作 Quick Worker 服务的运行契约，而不是某个页面或某个脚本的说明：

1. Quick Worker 属于 AI Runtime 层，是与 Chub Web 独立运行的本机后台服务。核心层入口负责认证、业务校验、提交和页面投影；Worker 负责任务、Session 租约、Runner 进程、超时、取消、恢复和最终状态。
2. 页面快速交互、经第三方服务层进入的微信 Chub 非实时任务和翻译任务都进入 Worker；核心层入口不得回退执行。当前生产只允许固定的 `codex` Runtime Runner。
3. 同一逻辑 Session 只能有一个 writer。快速交互提交、实时终端建立连接和恢复流程都必须经过 Worker 租约与实时连接的最终仲裁，不能只依赖页面按钮状态；当前 Quick Worker 在自己的 Session 租约内可以完成自己的原生 ID 绑定，其他 writer 不得被接管。内部翻译 Session 仍复用逻辑 Session，但允许在旧 native Session 空闲且新 ID 未被占用时轮换绑定。
4. Web 重启不会主动停止 Worker 或已接受任务。新 Web 必须完成 Worker 健康、协议、活动任务、租约、通知和重启状态恢复后，才开放快速交互 Session 写入；实时终端按独立的 Codex PTY/tmux 状态恢复。
5. 普通任务提交、取消和交付在 Worker 或状态不可确认时必须失败关闭：不猜测任务成功、不重复提交、不在 Web 内执行、不切换 Session 或通知收件人。固定的 Worker 重启是恢复入口，允许在不可用状态下尝试重建，但最终结果仍必须确认。
6. 任务终态、通知终态、Web 重启终态和 Worker 重启终态分别确认；“进程已创建”“任务已受理”或“HTTP 200”都不是成功。
7. 普通 Web、Worker、ClawBot 重启彼此独立；系统升级与恢复是单独的 AI Runtime 维护流程，会按其规则停止和清理 Worker，不由本文的普通重启门禁推导。

AI Agent 修改或排障时，先判断问题是否属于 Worker 服务；再读取本文对应章节，不能把实时 Session、OpenClaw 路由或系统升级的规则复制到 Worker 内部。所有命令、路径、Runtime 和服务入口均以固定后端配置为准，客户端输入不能扩大权限。

## 1. 服务定位与落地结论

Chub 的非实时 AI 任务已经从 Web 进程中拆出，由 AI Runtime 层的独立 Worker 承载。页面快速交互、微信 Chub 模式任务和翻译任务统一进入 Worker；当前唯一生产 Runtime 仍是 Codex，实时 Session 继续由 tmux 承载。第三方服务只能调用 AI Runtime 的公开提交与状态用例，不能直接操作 Worker 私有状态。

该方案解决以下核心问题：

- Web 重启不再中断正在运行的快速交互、微信任务或翻译任务。
- 新 Web 实例恢复后可重建任务投影、Session 占用关系、结果通知和重启状态。
- 同一 Session 仍保持单写入者约束，快速交互与实时连接不会并发执行。
- 任务请求重启时，只等待该任务自身结果及完成通知进入终态，不受其他 Session 或后续任务阻塞。
- 首页手动重启可直接接管待执行重启；同步与异步失败都会向用户展示明确原因。

独立 Worker 和跨 Web 重启方案的当前契约已完成维护者验收；当前 Worker 使用协议 `9`。错误原文透传和来源标记按本文 3.4 规则执行，协议或状态边界变化时按本文末尾复检规则重新验收。协议 `9` 及本次实时终端重连相关变化已完成 macOS/Ubuntu 服务重载后的真实最终状态复检。

## 2. 范围与边界

### 2.1 纳入范围

- 页面来源的非实时快速交互任务。
- 微信 Chub 模式提交的非实时任务。
- 由快速交互任务触发的翻译任务。
- Web 重启后的任务恢复、通知补偿和 Session 状态恢复。
- macOS LaunchAgent 与 Ubuntu systemd user service 的部署和维护边界。

### 2.2 不纳入范围

- 实时 Codex Session 的运行模型，仍由 tmux、实时连接和现有写入门禁负责。
- Worker 或宿主机崩溃后继续原进程执行。此类不确定任务按失败收敛，不自动重放。
- 跨设备迁移任务、远程 Worker 调度或分布式队列。
- 客户端直接提交任意命令、工作目录或自定义 Runner 参数；Codex 原生
  Session 的实际工作目录仍由 Worker 按可信原生 ID 重新发现，不受首页常用目录限制。

## 3. 独立服务架构

```text
Browser / WeChat / OpenClaw
             |
             v
      Chub Web / API
      - 认证与配置校验
      - 提交、查询与取消
      - 页面状态投影
      - 通知与重启协调
             |
             | local private protocol
             v
      Quick Interaction Worker
      - 任务与 Session 租约
      - Runner 生命周期
      - 翻译 FIFO
      - 权威任务状态
             |
             v
      fixed Runtime Runner registry
             |
             v
         Codex Runner
```

### 3.1 Web 职责

- 验证入口身份、Session、工作区和业务配置。
- 生成稳定任务 ID，并将任务提交给 Worker。
- 将 Worker 权威状态转换为页面、API 和通知状态。
- 在启动时完成恢复门禁，并在运行期间持续进行有界对账。
- 协调任务完成通知、任务级 Web 重启和手动重启接管。
- Worker 不可用或恢复未完成时关闭快速交互 Session 写入，不回退到 Web 内 Runner；实时终端不依赖 Quick Worker。
- `quick` Session 的新建同样要求 Worker 已就绪；这只限制首页的快速交互选项、快速交互页和微信 `new`，不限制 Runtime 可用时首页创建或进入实时终端。

### 3.2 Worker 职责

- 以独立系统服务运行，不随 Web 生命周期退出。
- 对任务 ID 提供幂等提交和结果查询。
- 原子管理 Session 租约，保证同一 Session 只有一个写入者。
- 管理 Runner 进程组、绝对超时、取消和终态落盘。
- 串行执行翻译任务并保存可恢复状态。
- 根据后端固定注册表解析 `runtime_id`，当前生产环境只允许 `codex`。
- 向 Web 暴露受限的本机协议，不承担页面、认证和通知路由职责。

### 3.3 Runner 边界

- Worker 通过最小 Runtime Runner 契约校验能力、准备固定进程规格、解释原生事件和读取规范化结果；Worker 本身继续拥有任务、租约、进程组、超时、取消、恢复和终态。
- 当前生产只注册 Codex Runner；固定测试 Runner 不进入生产组合，也不提供用户入口。
- 命令和参数来自后端固定配置；首页常用工作区只提供便捷创建入口，原生 Session 的实际工作目录由 Worker 通过可信原生 ID 重新发现，客户端不能提交路径。
- Codex Runner 的非交互式快速交互固定使用 `--skip-git-repo-check`，不要求普通工作目录预先执行 `git init`。该参数不是权限放宽：工作目录仍必须来自 Chub 已选工作区或经 Runtime Adapter 校验的原生 Session，审批、沙箱和单 writer 规则继续生效，客户端不能覆盖此参数。
- 任务正文通过受控输入传递，不拼接任意系统命令。
- 取消和超时作用于完整进程组，不能只结束父进程。
- 子进程成功创建只代表任务开始，不代表任务成功。

### 3.4 Runtime 错误传播

Quick Worker 不翻译不同 Runtime 的错误，也不要求维护一套通用错误码表。错误传播规则如下：

- 每个 Runtime Runner 必须提供 `read_error(task_dir, max_bytes)`，从自身事件流或错误输出读取上游错误原文；未知错误可以原样保留，不需要先注册 Chub 错误码。
- Runner 非零退出时，Worker 先读取该 Runtime 的错误原文；读取成功但没有错误文本时再回退到 `stderr`，两者均为空时才记录通用的 `runner_failed` 与默认提示。读取过程本身抛出 Runtime/解析错误时，保留其错误码和诊断并标记为 `chub`，不能改写成通用启动错误。
- 读取结果写入现有任务失败终态，沿用任务查询、页面时间线和通知链路返回；失败终态同时保存 `error_source`：`runtime` 只表示 Runtime 子进程提供的原文，`chub` 表示 Chub/Worker/解析边界自身的错误。取消和超时是任务终态，不是错误来源，`error_source` 必须为空；失败但来源缺失时保留为空，由页面显示“来源未确认”，不得默认归类。文本受任务目录权限、固定字节上限和现有错误字段限制，并按纯文本展示。透传只保留上游诊断文本，不透传配置 Token、`Authorization`/`Bearer` 凭证或终端票据；这些值在 Runner 和任务终态边界统一脱敏。
- Web 页面和通知使用固定来源标签：`runtime` 为“Codex CLI（上游 Runtime）”（英文通知为 `Codex CLI (upstream Runtime)`），`chub` 为“Chub”。只要任务没有进入 `failed`/`needs_terminal`，页面不显示错误来源；API 控制面错误按 `error.source` 展示，不把来源标签拼入底层错误原文。
- 错误原文不能作为命令、HTML 或新的任务输入执行。错误解析失败不得阻止失败终态、租约释放或恢复对账，也不得自动重放任务。
- 新 Runtime 只需实现同一 Runner 错误读取契约即可保留自身错误文本；Worker 继续拥有任务终态、超时、取消、租约和恢复。

## 4. 任务与 Session 业务逻辑

### 4.1 权威状态

Worker 任务状态按以下方向单向推进：

```text
accepted -> starting -> running -> succeeded
                              \-> failed
                              \-> timed_out
                              \-> cancelled
```

翻译任务可在执行前进入 `queued`。终态不可回退，也不会因 Web 重启重新执行。

各类状态的权威来源保持分离：

- 快速交互任务、翻译任务和对应 Session 租约：Worker。
- Codex Session 映射与基础元数据：AI Session Store。
- 实时连接、tmux 和实时写入者：现有实时连接管理。
- 页面 `working`、结果提示和时间线：Web 对权威状态的可恢复投影。

### 4.2 提交与幂等

1. Web 校验请求并生成稳定任务 ID。
2. Worker 在同一原子操作中检查任务幂等性和 Session 租约。
3. 租约可用时接受任务；已有快速任务或实时写入者时返回明确冲突。
4. Web 在提交响应不确定时使用相同任务 ID 查询，不创建第二个任务。
5. Worker 进入终态后释放租约，Web 再恢复 Session 的可提交状态。

首次创建 Session 时，Web 与 Worker 共同使用预分配的稳定 Session ID。Runner 返回的真实 Session 信息只能补全映射，不能改变任务归属或绕过租约。

### 4.3 单写入者约束

- 快速交互提交前同时检查 Worker 租约和实时写入者。
- 实时连接建立前同时检查 Worker 租约和现有实时连接。
- 两侧并发竞争由原子租约和最终仲裁收敛，不能只依赖页面按钮状态。
- 取消、失败、超时和恢复都必须最终释放租约；异常租约由对账流程清理。

### 4.4 启动恢复与常驻对账

新 Web 实例启动时可以先提供健康检查，但快速交互 Session 写入门禁保持关闭，直至完成以下恢复；实时终端不使用这道 Quick Worker 门禁：

1. 校验 Worker 健康、协议版本和 generation。
2. 拉取活动任务、近期终态、Session 租约和翻译状态。
3. 重建页面快速交互状态、Session 占用和结果投影。
4. 恢复未完成通知与重启状态。
5. 清理可确认的过期投影后开放 Session 写入。

Web 运行期间继续进行有界对账，用于处理断线、短暂不可用和通知补偿。若 Worker 不可达、协议不兼容或状态无法确定，快速交互 Session 写入继续失败关闭；实时终端和不涉及 Quick Worker 的功能按各自状态继续服务。

Worker 对已交付终态保留有限历史或墓碑，直到 Web 明确确认已消费，避免短暂离线造成结果丢失。

### 4.5 翻译任务

- 翻译与主任务使用同一 Worker，但采用独立 FIFO 队列。
- 每个快速交互任务在受理时快照当前 Chub Session 的权限、模型和推理等级；Worker 及恢复流程只使用任务快照，之后修改 Session 配置不改写已经排队或运行的任务。模型或推理等级为空时由 Runtime 使用默认配置；翻译任务仍使用其独立的任务级模型和推理等级。
- 启用微信文本优化时，普通任务先进入翻译 FIFO；成功解析中文润色与 English 后，才以持久化派生标识提交到原业务 Session。固定指令不进入该队列。
- 翻译状态保存原消息标识、可信回送路由、目标 Session/槽位和结果；确认队列与翻译 FIFO 使用同一持久化状态。普通 Web 重启不清空任一队列，也不会重复提交已经存在的翻译或主任务。
- 翻译成功后目标 Session 失效时丢弃，不自动切换；目标暂忙时保留固定目标并等待执行层可写后重试。自动执行任务视为已确认，确认模式任务仍先进入确认 FIFO；翻译失败不回退执行原文。
- 翻译任务可靠进入 Worker 后，微信同步链路静默结束；只有润色文本对应的主任务也被 Worker 接收后，Web 才进入 `Started` 通知流程。
- 润色中文和 English 各自最多 8000 字符，超限按翻译失败收敛且不得写入不可恢复状态。恢复时缺失 Worker 任务必须通知失败，并把源消息关闭为重复投递可静默重放的失败终态；派生主任务已经提交时按保存标识幂等确认，不得再次提交或改写为未提交。
- Worker 或宿主机异常导致执行结果不确定时标记失败，不自动重放。

## 5. 结果与通知

- 页面任务结果、微信结果和完成通知都以 Worker 终态为依据。
- 模型回复、Tool Call 已创建或 Runner 已启动均不等同于任务成功。
- `Started` 与主任务终态通知独立持久化和投递，不增加先后门禁；极快主任务可能出现轻微到达乱序，不影响 Worker 权威终态。
- 微信通知的用户可见格式、状态尾部和额度展示由[集成能力清单第 4 节](CHUB_INTEGRATION_CAPABILITIES.md#4-微信-clawbot-指令)维护；Worker 只负责通知记录和终态，不拥有展示文案。
- 微信任务保存本次任务的账号和发送者路由，结束后只按该路由发送，不回退到全局接收人。
- 页面来源快速交互不发送微信完成通知，结果只在页面时间线展示；只有保存了微信 ClawBot 原路由的任务才投递微信完成通知。
- 通知记录区分 `pending`、`sending`、`succeeded` 和 `failed`。
- `pending` 可由新 Web 实例继续投递；旧实例退出时停留在 `sending` 的未知结果按失败收敛，不自动重发，避免重复通知。
- Web 只有在通知进入明确终态后，才认为该任务的通知阶段结束。

## 6. 重启与恢复语义

### 6.1 普通维护重启

电脑端 `chub restart`、微信固定 `restart` / `restart web` 或首页手动重启都只重启 Web 服务。Worker、正在运行的 Runner、翻译 FIFO、确认 FIFO 和实时 tmux Session 保持不变；新 Web 恢复已送达的确认队头与已确认但等待目标可写的任务，且不重复提交。实时终端的旧 `ttyd` 桥由旧实例关闭或新实例启动时清理，用户再次进入 Session 时重新创建桥并 attach 原 tmux。微信 `restart worker` 才执行 Quick Worker 的任务清理与恢复，不影响 Web 或实时终端。升级/恢复清理后的旧逻辑映射则按升级操作保存的旧逻辑 ID 与原生 Session ID 重新绑定仍存在的 Chub tmux。实时终端的完整重连规则以[AI Session 状态模型设计](AI_SESSION_STATE_DESIGN.md)和 Runtime 设计为准。

新实例健康后通过启动恢复门禁重建状态，再恢复 Session 写入和页面操作。

### 6.2 任务请求重启

快速交互任务需要重启 Chub 时，只调用一次 `scripts/chub-web-restart`。快速交互环境将其登记为受协调的重启请求，脚本不直接中断当前任务。

重启触发条件为：

1. 发起请求的主任务已经成功。
2. 该任务自己的完成通知已经进入成功或失败终态。
3. 当前没有同一轮已启动的重启需要复用。

满足条件后直接执行 Web 重启。其他 Session、其他快速任务、实时连接和翻译任务均不构成阻塞条件，也可以在等待期间继续提交和运行。

重启请求按“当前轮次 + 下一轮次”合并：

- 当前轮尚未启动时，多个请求合并到当前轮。
- 当前轮已开始后，新请求登记到下一轮。
- 主任务失败、取消或超时时，不提升其重启请求。
- 已被同一成功重启覆盖的请求不再重复执行。

新实例健康并确认实例 ID 已变化后，才记录重启成功。仅看到重启子进程创建或旧进程退出不能宣告成功。

### 6.3 首页手动重启

- 手动重启不受其他活动 Session 或快速任务阻拦。
- 若存在尚未开始的任务重启，手动操作接管该轮，避免重复重启。
- 若重启已经开始，页面复用当前操作状态，不再创建第二次重启。
- 同步提交失败在重启对话框内展示；异步启动失败、超时或健康确认失败通过“工作站环境”的 Chub Web 分区和操作时间线展示。
- 重启命令未创建、返回明确的正退出码，或被信号终止且宽限期后旧实例仍然存活时记为 `start_failed`，
  保存具体原因并结束本轮，不自动重试。
- 服务管理器停止旧实例时可能同时终止其重启命令子进程；负信号退出先等待旧实例退出，不立即误判失败。旧实例在宽限期后仍存活时，才记录信号或退出码等具体原因并展示给用户。

`restart_sensitive` 仅为协议兼容和历史数据保留，不再参与是否允许重启的门禁判断。

### 6.4 首页 Quick Worker 启停与重启

- 首页只展示经过裁剪的 Worker 状态，不暴露 PID、generation、运行时明细或本机路径。
- Worker 重启是恢复入口，不以 Worker 健康、协议状态、排队任务、执行中任务或 Web 恢复状态作为开始门禁；只保留重复维护和系统升级直接冲突等最小保护。
- 确认重启后立即关闭新任务提交；排队任务进入 `cancelled`，执行中任务停止完整 Runner 进程组并进入 `failed/worker_restarted`，相关 Session 租约释放，任务不自动重放。任务记录保留，不静默删除。
- 后端只调用固定的 `worker-reload`：能连接当前协议 Worker 时先通过重启标识的 drain 原子关闭提交并终止任务；Worker 不可达或协议不兼容时直接重载服务，不迁移旧协议运行态，新实例只接管当前协议数据。
- 重启成功必须确认新 generation、当前协议、Worker 健康和任务计数归零；不能只以子进程创建或 HTTP 受理为成功。Web 只负责发起、展示和恢复后对账，不作为 Worker 重启成功的健康门禁。
- Quick Worker 与 Web、ClawBot 服务重启彼此独立；Worker 重启期间只影响快速任务提交、租约和结果写入，其他首页只读能力、Web 服务、ClawBot 和实时终端继续可用。
- 操作记录完整保留 `requested`、`started`、`succeeded` 和 `failed`；Web 在操作过程中重启后，依靠持久化状态、新 generation 和进程校验收敛最终结果。
- 系统升级与恢复最终确认 Worker 健康后，升级结果优先于更早的独立 Worker 重启终态；仅清理已结束的 `succeeded`/`failed` 当前状态投影，不删除历史操作日志，也不清理仍处于 `requested`/`started` 的操作。这样页面展示当前可用状态，不会把已恢复服务继续显示为旧的重启失败。
- 首页停止态只显示“启动”，运行态显示“重启”和“停止”；状态未知、协议不兼容或不可达时不直接显示为停止。`worker-start` 和 `worker-stop` 只控制 Worker 服务并确认服务管理器最终状态；停止要求没有排队或执行中的任务，失败时保留原状态和恢复路径。
- “重启”是页面短按钮文案；其确认说明必须保留取消排队任务、停止执行中任务且不自动重放的影响。启动、停止和重启均记录 `requested`、`started`、`succeeded` 或 `failed`，不能把命令进程创建当作成功。

## 7. 持久化、协议与安全

### 7.1 Worker 数据

- 每个任务使用私有目录和独立元数据、输入、输出与错误文件。
- 目录权限为 `700`，文件权限为 `600`。
- 元数据使用临时文件加原子替换，避免半写状态。
- 所有读取都限制任务数、文件字节数和单行长度。
- generation 标识 Worker 运行世代，用于 Web 判断是否发生 Worker 重启或状态失效。
- 当前协议 `9` 的任务使用通用 `runtime_id`、`native_session_id` 和 Worker 生成的 `execution_id`；同一次执行的状态、规范化 Runtime 事件和终态保持同一执行标识。失败终态可带受控的 `error_source`，用于区分 Chub/Worker 与上游 AI Runtime；它只属于任务详情和 Web 投影，不扩展 Worker 健康响应。Runtime 能力矩阵只属于后端 Adapter/Runner Registry 契约，由契约测试验证；不开放客户端 Runtime 选择。
- Worker 只读取和写入当前协议 `9` 的版本目录。升级维护按固定方案删除源协议及操作记录中确认的旧协议任务、墓碑、租约和交付覆盖目录，不迁移、查询、恢复或重派旧数据，也不在 Worker 内保留旧协议模型和兼容开关。

### 7.2 Web 数据

- 页面投影、通知状态、操作记录和重启协调状态由 Web 侧持久化。
- Web 数据不是 Worker 任务终态或 Session 租约的权威来源。
- 新实例必须通过对账恢复，不能仅依赖旧实例内存状态。

### 7.3 本机协议

- Web 与 Worker 通过本机私有 Unix socket 通信。
- socket 和父目录仅允许服务用户访问，并校验真实 peer credential。
- 不信任客户端转发 Header，也不接受任意路径、命令或环境变量。
- Web 与 Worker 必须使用完全兼容的当前协议版本；不兼容时关闭写入并提示升级，不做模糊降级。

## 8. 跨平台部署与维护

| 能力 | macOS | Ubuntu |
| --- | --- | --- |
| Web | LaunchAgent | systemd user service |
| Worker | 独立 LaunchAgent | 独立 systemd user service |
| Web 重启 | 重载 Web 服务 | 重启 Web user service |
| Worker 重启/升级 | 停止任务后重启 Worker | 停止任务后重启 Worker |
| 任务处理 | 排队任务取消，执行中任务失败收敛 | 排队任务取消，执行中任务失败收敛 |

维护约束如下：

- Web 与 Worker 使用不同服务单元，普通 Web 重启不得带停 Worker。
- Worker 重启或代码升级使用固定 `worker-reload`；它先关闭新提交并清理当前任务，再独立重载服务以及确认协议、generation、损坏记录和固定 Runner 健康状态。该操作只影响 Worker 自身任务资源，不等同于 Web 或 ClawBot 互斥。
- 协议升级需要 Web、Worker、测试和部署产物同步发布；首页系统升级与恢复流程会停止 Worker、清理旧协议运行态、重建固定服务定义并确认目标协议和空闲健康状态，旧任务会被中断且不会迁移或自动重放。该流程只恢复 Debug Chrome Supervisor 服务，不自动启动 Debug Chrome 浏览器实例；浏览器实例状态由工作站环境单独确认，自动化环境只负责使用已准备好的实例执行飞书任务。普通 Worker 重启遇到协议不兼容时只负责重建当前 Worker，不代替系统升级流程。
- `chub install`、`chub stop` 和 `chub uninstall` 遇到活动或排队任务时必须明确拒绝；确认空闲后仍需通过原子 drain 关闭提交门禁再执行操作。只有维护者确认任务可以中断时才使用对应命令的 `--force`。
- Web 与 Worker 分别写入独立操作日志；日志详情页提供两个固定来源，避免两个常驻进程共同轮转同一文件。

## 9. 失败语义

| 场景 | 预期结果 |
| --- | --- |
| Web 重启或短暂不可用 | Worker 任务继续；新 Web 恢复后重建状态 |
| Worker 不可达 | 快速交互 Session 写入失败关闭；不回退到 Web Runner，实时终端不受该门禁影响 |
| Worker 已返回终态但原生 Session 映射冲突 | 普通 Session 或不满足安全轮换条件的翻译 Session 失败收敛、释放其占用并确认 Worker 交付；内部翻译 Session 仅在旧 native Session 无活动 writer 且新 ID 未被其他 Chub Session 占用时更新当前绑定并继续交付；不把单任务冲突扩散为全局恢复失败 |
| Runtime 发现与 Worker 原生 Session 绑定竞态 | 活动 Quick Session 的同工作目录发现结果暂缓导入；若历史自动发现记录已形成重复，当前 Worker 自己仍持有 writer 时允许完成归属回收，其他 terminal/external writer 则失败关闭 |
| Worker 协议不兼容 | 拒绝写入并提示同步升级 |
| Worker 重启 | 排队任务取消，执行中任务以 `worker_restarted` 失败收敛，不自动重放 |
| Worker 异常退出 | 不确定的运行任务标记失败，释放租约，不自动重放 |
| 宿主机重启 | 不承诺原任务继续；恢复后按持久化状态收敛 |
| Runner 超时或取消 | 结束完整进程组，记录明确终态并释放租约 |
| Runtime 返回上游错误 | 保存并展示该 Runtime 的错误原文；读取为空时回退 `stderr`，解析失败时展示 Chub Runtime 错误码和诊断；只有没有任何诊断时才使用通用 Runner 错误 |
| 通知发送失败 | 记录失败终态；不影响主任务真实结果 |
| Web 重启启动失败 | 记录 `start_failed` 或健康确认失败，页面显示原因 |
| 新 Web 未完成恢复 | 健康检查可用，Session 写入继续关闭 |

一个固有边界是：旧 Web 已退出而新 Web 又无法启动时，Chub 自身无法继续发送最终页面或通知结果，必须依赖 LaunchAgent、systemd 或外部维护手段恢复服务。该情况不能伪造成重启成功。

## 10. 已验收基线

当前验收基线包括：

- 页面快速交互、微信 Chub 模式和翻译任务均由独立 Worker 承载。
- Web 重启期间任务持续运行，新实例恢复结果、Session 占用和通知状态。
- Quick Worker 只接收 `session_mode=quick` 的 Session；`terminal` Session 在 Web/Worker 边界被拒绝。两类 Session 不互相接管 writer，同一 `quick` Session 仍由租约保证最多一个快速任务写入者。
- Worker 不可用、协议不兼容或恢复未完成时，快速交互 Session 写入保持失败关闭；实时终端使用独立的 Codex PTY/tmux 链路，不因 Quick Worker 恢复状态暂停。两类入口仍通过 Session 类型和单 writer 规则隔离。
- 实时终端的 `ttyd` 只是可重建的 Web 桥，固定 tmux carrier 和原生 Codex writer 不由普通 Web 重启终止；升级/恢复按持久化操作关联自动迁移仍存在的 Chub tmux，无法确认归属时仍失败关闭。
- 任务请求重启只等待自身结果与通知，不等待其他任务、Session 或翻译。
- 等待重启期间仍可提交任务；跨重启边界的请求按当前轮和下一轮正确合并。
- 首页手动重启可接管待执行重启，失败原因对用户可见。
- 首页可独立查看 Quick Worker 状态；确认后可在 Worker 健康、忙碌、协议不兼容或不可达时执行受控重启，重启只清理 Worker 自身任务并不把 Web 或 ClawBot 服务重启串行化。
- 重启成功以新实例健康和实例 ID 变化为准，操作日志状态完整。
- Ubuntu 已实际确认 Web/Worker 服务边界、安装维护、活动任务保护、系统升级服务切换和本次协议/终端重连后的最终状态；macOS 的既有 Web/Worker 契约保留自动化覆盖，但未在本轮重新实机复检，其他未实际复检的平台不自动获得支持承诺。
- 权限、固定命令、受限读取、幂等提交和敏感信息保护未退化。

维护者已完成独立 Worker 与跨 Web 重启的当前业务契约验收；历史协议基线只用于追溯，不作为当前支持协议。后续若修改任务权威来源、Session 租约、通知终态、重启门禁或服务关系，必须按当前协议重新完成相关回归。

### 10.1 验收范围与复检

- 已验收范围：独立 Worker、Web 重启恢复、快速交互/微信/翻译任务、Session 租约、任务和通知终态、固定 `codex` Runner、当前协议 `9` 的自动化和契约回归，以及系统升级脚本对 Worker 协议与服务定义的模拟编排。
- 未验证或不承诺：第二个真实 Runtime、其他未实际复检的平台，以及本文没有列出的协议兼容或任务迁移能力；自动化覆盖不得替代未完成的平台实机验收。
- 复检触发：任务权威来源、租约、恢复屏障、通知终态、协议版本、Runtime 能力矩阵、重启门禁或 Web/Worker 服务关系变化时，必须按当前协议重新验证最终状态、操作日志和失败关闭边界。
