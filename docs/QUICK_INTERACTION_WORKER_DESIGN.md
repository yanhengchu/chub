# Chub 快速交互独立 Worker 与跨 Web 重启恢复设计

> 状态：进行中。本文描述已确认的目标方案；步骤一至步骤三已完成代码与隔离验证，当前暂停在步骤四之前，
> 等待 Ubuntu 同步部署及前三步验收。Worker 尚未接管正式快速交互，当前任务行为仍以
> `AI_SESSION_STATE_DESIGN.md` 和实际代码为准。

## 1. 目标

本方案只解决一个核心问题：

> 将快速交互任务的执行生命周期从 Chub Web 进程中独立出来，使 Chub Web 重启不会中断正在执行的
> 快速交互，并在新 Web 实例启动后恢复准确的 Session 状态和任务结果。

具体目标：

- 快速交互由独立 Worker 启动和监管，不再由 Web 进程持有。
- Chub 后台发起的所有非实时 Codex 执行统一使用 Worker，包括页面快速交互、微信 Chub 任务和翻译任务；
  不保留 Web 内 Runner 或按任务类型分流的旧执行路径。
- Web 重启时，Worker、`codex exec resume` 和正在执行的 Turn 继续运行。
- 新 Web 能恢复运行任务、Session 占用、原生 Codex Session ID、最终结果和必要通知状态。
- 恢复期间不能把相关 Session 误判为空闲，不能向同一 Session 提交第二个 Turn。
- 其他空闲 Session 可以继续使用，不因一个快速任务或待重启状态形成节点级提交门禁。
- 任务不因 Web 重启重新计时、重复执行、丢失结果或重复通知。
- macOS 和 Ubuntu 对上述行为提供一致的产品语义。

### 1.1 不在本次范围内

- 不调整实时交互架构。实时交互继续由 tmux 承载，Web 重启只会断开页面连接，不中断 Codex Turn。
- 不保证 Worker 崩溃或整机重启后任务继续执行，也不自动重放不确定任务。
- 不建设通用任务队列、数据库、分布式调度或高可用 Worker。
- 不迁移浏览器自动化、维护任务等其他 Web 进程内后台任务。
- 不迁移切换前已有 Session 的运行态、快速交互任务或占用关系，也不提供新旧执行协议兼容层。首次部署
  Worker 并准备重启验收前，由维护者确认没有执行中任务并归档当前 Session，重启后使用新建 Session 验收。
- 不在首期建设版本化部署快照、通用工作区写入检测或完整的并发开发治理。
- 不把 Worker 升级和 Web 重启合并成同一种操作。

这里的“所有非实时 Codex 执行”指会创建或续接 Codex Turn、产生任务结果的后台执行。模型目录、用量读取
等只读元信息探测不创建 Turn、不占用 Session，不纳入 Worker 任务协议；实时终端继续由 tmux 承载。

## 2. 当前问题与既有边界

当前快速交互由 Chub Web 直接创建并监管 `codex exec resume`。Web 关闭时会结束 Runner，并把未完成任务
标记为失败。现有延迟重启因此必须等待节点上的全部快速交互结束，并在等待期间拒绝新任务。

实时交互不同：Codex 运行在 tmux 中，ttyd 和 WebSocket 只是连接层。Web 重启后重新进入页面即可继续
查看同一个 Session，当前不需要特别改造，只需保留状态探测和回归验证。

本方案把“页面连接”“Chub Session 状态”和“Codex Turn 执行”分开：

- 页面与 Web 是可重启的控制面。
- Chub Session 是持久化映射，不等同于某个 Web 进程。
- 快速交互 Execution 由 Worker 持有；实时交互 Turn 仍由 tmux/Codex 持有。

### 2.1 首次切换边界

首次从 Web 内执行切换到独立 Worker 时不做运行态数据迁移：

- 维护者先结束或等待当前快速交互与实时 Turn 完成，并手工归档现有 Session。
- 安装或启用 Worker 前执行只读预检；发现执行中任务或未归档 Session 时拒绝切换并给出明确提示，不自动
  停止任务、不自动归档或删除 Session。
- 既有归档记录和原生 Codex 历史继续按当前规则保留，但不恢复为 Worker 托管任务，也不参与新 Session
  的租约状态。
- 切换是一次性切换：Worker 健康和预检通过后，所有新的非实时 Codex 任务全部走 Worker；不保留按配置
  回退 Web 内执行的双轨模式。Worker 不可用时明确失败关闭。
- 切换完成后新建的 Session、页面/微信快速交互和翻译任务全部遵循本文协议；后续普通 Web 重启必须支持
  这些新任务恢复。

因此首期不需要为旧任务状态文件设计双格式迁移、旧 Runner 接管或历史 Session 租约转换。这里省略的是
首次部署迁移，不是日常 Web 重启恢复能力。

## 3. 总体架构

```text
页面 / OpenClaw
       │
       ▼
Chub Web ──本机私有 IPC──> Quick Worker ──固定参数──> Codex Runner
   │                              │                         │
   ├─认证、API、页面与通知         ├─执行、超时、取消          └─单次 codex exec
   └─Session 状态投影与结果合并     └─Session 租约与最终记录

实时交互页面 ── Chub Web / ttyd ── tmux ── Codex TUI
                                      （保持现状）
```

### 3.1 Chub Web

- 校验认证、权限、Session 配置和提交参数。
- 为页面或微信请求建立稳定任务 ID 和幂等关联。
- 通过本机 IPC 向 Worker 提交、查询和取消任务。
- 将 Worker 的执行状态投影到现有 Session 和快速交互页面。
- 发送完成通知，并在新实例恢复后处理尚未开始的通知。
- 启动恢复后继续运行常驻对账器，不依赖页面轮询或新请求来发现任务完成。
- 在 Session 操作前查询 Worker 的执行租约；Worker 不可用或状态未恢复时失败关闭。

Web 不再启动、持有或终止快速交互 Runner，也不能把自身关闭解释为任务失败。

### 3.2 Quick Worker

- 作为独立用户服务常驻，生命周期不依赖 Web。
- 按任务 ID 幂等接受任务并持久化后，才确认提交成功。
- 原子持有 Session 执行租约，同一 Session 同时最多一个快速交互 Turn。
- 使用固定规则启动、监管、超时和取消 Runner。
- 保存绝对截止时间、Runner 身份、原生 Session ID、最终结果和退出状态。
- 持有翻译的有界顺序队列、排队截止时间和独立 Session 演进，不由 Web 线程二次调度。
- 是快速交互执行状态的唯一事实来源。
- 提供有界的提交、查询、列表、取消和健康检查接口。

Worker 不负责页面、网络认证或外部通知，不接受客户端提供任意命令、工作目录或结果路径。

Runner 启动使用最小握手：子进程先进入等待，Worker 保存任务与进程身份后再放行其执行 Codex。这样 Worker
不会在“Runner 已运行但身份尚未落盘”的窗口崩溃并留下无法识别的执行进程。

### 3.3 Codex Runner

- 每个快速交互任务对应一个受 Worker 管理的 Runner。
- 使用固定的 `codex exec --json --output-last-message` 调用形式。
- 提示词通过标准输入或 Worker 创建的私有输入文件传递，不拼接 Shell 命令。
- 标准事件、错误和最终消息写入任务专属私有目录。
- 收到取消或达到绝对截止时间时，由 Worker 终止对应进程组。

## 4. 状态与生命周期

### 4.1 三类状态必须分开

| 状态 | 权威来源 | Web 重启后的处理 |
| --- | --- | --- |
| 原生 Codex Session | Codex Session ID、持久 Session 映射 | 保留并重新对账 |
| 实时交互 Turn | tmux、Codex 原生 writer、可信 Hook | 保持现有探测逻辑 |
| 快速交互 Execution | Worker 任务和 Session 租约 | 从 Worker 恢复 |

页面展示的 `working/idle/unknown` 是对权威状态的投影，不是执行事实。新 Web 尚未完成对账时必须使用
`unknown` 或“状态恢复中”，不能按旧内存默认值显示为空闲。

### 4.2 快速交互状态

```text
accepted → starting → running → succeeded
                           ├──→ failed
                           ├──→ timed_out
                           └──→ cancelled
```

- `accepted` 表示任务规格和 Session 租约均已可靠保存。
- `starting/running` 在 Web 重启后仍保持非最终态。
- 最终态只由 Worker 根据 Runner 结果写入，不可回退。
- Worker 或 Runner 消失且没有完成记录时，任务标记为执行器异常中断，不自动重放。
- 超时使用提交时保存的绝对截止时间，Web 重启不能重新计时。

翻译任务同样整体迁入 Worker，并在上述模型前增加 `queued`。队列容量、顺序、排队截止时间、独立
Session 选择和执行启动均由 Worker 持久管理，Web 只提交、展示和对账。排队时不持有 Session
执行租约，开始执行时再原子取得；排队截止时间和执行绝对截止时间分别保存，Web 重启不重置。
翻译继续使用独立 Session、`read-only`、有界顺序队列和成功才通知的现有产品语义。首个翻译任务
产生原生 Codex Session ID 后，Worker 先持久更新翻译 Session 映射；后续已排队任务在实际启动时读取
最新映射，不使用入队时的旧 Session 快照。

### 4.3 首次快速交互创建 Session

首次快速交互可能在执行中才获得原生 Codex Session ID。Worker 必须从可信 Codex 事件或 Hook 中提取并
持久化该 ID，再由 Web 幂等合并到 Chub Session 映射。

如果任务已经完成但仍无法确认原生 Session ID：

- 保留真实任务结果；
- 将该 Session 标记为异常或状态未知；
- 禁止再次提交，等待人工通过实时终端或受控同步恢复；
- 绝不通过重新执行首个任务来找回 Session ID。

## 5. 提交、互斥与恢复

### 5.1 幂等提交

1. 微信继续使用可信消息标识；页面发送前生成一次性提交键，并在响应不确定时复用。
2. Web 为该幂等标识建立稳定任务 ID，保存公开任务骨架、操作关联和可信通知路由。
3. Web 向 Worker 提交任务 ID、规格摘要和受控 Session 快照。
4. Worker 原子检查任务 ID：相同 ID 和摘要返回原任务，不同摘要返回冲突。
5. Worker 持久化任务和 Session 租约后返回 `accepted`，Web 才向入口确认提交成功。
6. Web 在请求中途重启时，新实例使用原任务 ID 查询和补齐展示，不生成第二个 Runner。

页面提交键只影响幂等关联，不能用作文件名、路径或 Worker 权限凭据。

### 5.2 Session 单 writer

Worker 的 Session 租约是快速交互准入权威，Codex 原生 writer 是最终执行仲裁：

- 提交快速交互前，Web 可先检查状态以尽早反馈。
- Worker 在取得 Session 租约后、启动 Runner 前再次检查原生 writer。
- 实时终端输入、新快速交互、停止、归档和删除必须与 Worker 租约协调。
- Worker 不可用或新 Web 尚未完成租约恢复时，Web 无法证明哪些 Session 仍有 Runner。此时所有可能
  创建 writer 或改变 Session 生命周期的操作都失败关闭，包括实时终端启动或输入、快速提交、停止、
  归档和删除；只读查看和非 Session 功能仍可使用。
- 原生 writer 在检查与 Runner 启动之间出现竞态时，以 Codex 启动结果为准，任务安全失败，不启动第二次。

恢复完成并已获得完整租约视图后，约束只作用于冲突 Session；其他 Session 和非 Session 功能不受影响。

### 5.3 Web 启动恢复屏障

新 Web 对外提供健康检查后，快速交互和冲突 Session 操作仍需经过一个短暂恢复屏障：

1. 连接 Worker 并确认协议兼容。
2. 读取运行任务和 Session 租约。
3. 将相关 Session 投影为 `working + quick`。
4. 合并已完成但尚未进入公开历史的最终结果。
5. 恢复原生 Session ID 和尚未开始的通知状态。
6. 完成后开放快速交互及相关 Session 变更操作。

Worker 不可用时，其他页面和无关功能可以正常提供；快速交互及所有 Session writer/生命周期变更操作明确
返回 503，不回退为 Web 进程内执行。

### 5.4 运行期对账与交付确认

启动恢复屏障只负责新 Web 实例的首次收敛。屏障完成后，Web 还必须保持一个常驻、有界的 Worker 对账器：

- 按固定短周期或单调变更游标读取增量任务，每次响应限制条数和字节，不依赖页面轮询触发。
- 任务最终结果、原生 Session ID、Session 状态投影、操作日志和通知状态分别使用稳定幂等键合并；
  某一步失败不回滚 Worker 最终态，后续周期继续收敛。
- Web 只在公开历史和必要交付元数据可靠落盘后，向 Worker 写入交付确认游标；通知另按自身状态机处理，
  通知失败不阻止执行结果对账。
- Worker 完成记录在获得 Web 交付确认前不得清理；Web 重启或短时失联只导致延迟交付，不丢失结果。

## 6. 持久化与本机协议

Worker 使用每任务独立目录，避免与 Web 并发改写同一个聚合 JSON。至少保存：

- 提交规格：任务 ID、Session ID、完整私有 Prompt、Prompt 摘要、受控执行配置、创建时间、绝对截止时间和
  协议版本。
- 执行状态：`accepted/starting/running`、Worker generation、Runner 身份和更新时间。
- 完成记录：最终状态、退出码、结果摘要、完成时间和原生 Session ID。
- Worker 私有执行关联：Runner 身份和受控执行上下文。

Web 继续在独立的交付状态中保存操作 ID、非敏感来源标识、可信微信路由、通知和页面展示元数据。Web 不
改写 Worker 的执行状态或完成记录，Worker 也不改写 Web 的通知状态；两侧只通过任务 ID 和完成记录摘要
幂等对账。所有私有文件保持 `600`，目录保持 `700`。

写入使用临时文件加原子替换；任务接受和最终完成在必要落盘成功后才对外确认。Worker 对任务 ID 与规格
摘要做原子幂等检查：同 ID、同摘要返回原任务，同 ID、不同摘要返回冲突。任务 ID 必须包含 Worker 可校验的
创建时间和足够随机性；完成记录对账后可删除大体积事件和结果文件，但必须在固定提交重试窗口内保留
小型幂等 tombstone。Web 的提交键保留期不得长于 Worker tombstone 保留期；超出重试窗口的任务 ID 在
Web 和 Worker 两端都只能拒绝，不得生成新 Runner。保留期、单任务 Prompt、事件、结果和错误上限必须使用
固定配置或代码常量，在协议双端一致校验，不接受客户端放宽。

进程存在只表示任务可能仍在运行，不能代替完成记录；Runner 身份不能只依赖可能复用的 PID。首次创建
Session 时，Worker 以自己持有的 `codex exec --json` 可信事件为原生 Session ID 的主来源并先行落盘；
Hook 只作交叉校验或补充，不是跨 Web 重启恢复的唯一来源。

Web 与 Worker 使用固定短路径的 Unix Domain Socket：

- 仅监听本机，不开放网络端口。
- Socket 所在目录权限为 `700`。
- macOS 使用 `getpeereid`，Linux 使用 `SO_PEERCRED` 校验同一用户。
- 协议只提供固定动作和结构化字段，不接受任意命令或路径。
- Web 与 Worker 只接受当前完全一致的协议版本，不实现相邻版本或旧格式兼容；版本不一致时快速交互失败关闭。

## 7. Web 重启与通知

### 7.1 普通 Web 重启

1. 只重启 Web 服务，不向 Worker、Runner 或 tmux 发送停止信号。
2. 页面、API 和 WebSocket 在重启窗口内短暂不可用。
3. Worker 继续执行快速交互并持久化进度或结果；实时交互由 tmux 继续执行。
4. 新 Web 完成第 5.3 节的恢复屏障后，页面显示真实状态。

### 7.2 快速任务请求重启

快速任务继续只调用一次 `scripts/chub-web-restart`。在快速交互环境中，该脚本只为当前任务登记重启意图，
不在 Turn 中途重启：

1. 请求任务成功结束并持久化结果。
2. 该任务自己的完成通知进入已发送、失败或跳过终态。
3. Chub 启动一次 Web 重启，不等待其他普通快速任务。
4. 其他 Worker 任务继续运行；新 Web 恢复后继续显示和接收结果。
5. 新实例完成恢复屏障后，回写重启结果；微信来源按任务保存路由发送独立重启通知。

失败、取消或超时任务登记的重启意图不执行。多个同时满足条件的重启意图可以合并为一次实际 Web 重启；
已开始重启后才完成的请求不由前一次重启提前满足。实现只需串行保存“当前重启”和“下一次待重启”，无需
建设通用 generation 队列。

通知继续采用独立状态和至多一次语义：

- 通知失败不改变任务最终结果。
- `pending` 可由新 Web 接管。
- 已进入 `sending` 后发生重启，其不确定结果标记失败，不自动重发。
- 微信任务始终使用任务提交时保存的账号和发送者，不回退全局接收人。

### 7.3 轻量重启安全保护

普通任务不阻塞 Web 重启。首期不依赖 Agent 在修改中途主动登记：任务提交时，只要目标 workspace 是
Chub 工作区且权限不是 `read-only`，Web 便在受控任务规格中设置 `restart_sensitive=true`，Worker 从
`accepted` 到最终态持久保持该标记。自动 Web 重启等待这些任务结束，避免加载明显处于修改过程中的文件。

首期保持简单：

- 标记只影响自动 Web 重启，不阻止其他任务提交和执行。
- 标记随 Worker 任务状态持久化，任务正常结束后清除。
- 标记任务异常结束时取消本次自动重启并提示维护者检查，不设计孤立锁恢复页面或通用工作区锁。
- 不尝试检测任意外部进程写入，也不负责解决多个任务并发修改同一工作区。

这是附加保护，不参与 Session 状态恢复，也不是独立 Worker首期主体。若实际使用证明需要更强约束，再单独
设计排他租约或版本化部署。

## 8. 双平台服务边界

| 能力 | macOS | Ubuntu |
| --- | --- | --- |
| Web | `com.chub.node` LaunchAgent | `chub.service` user unit |
| Worker | 独立 LaunchAgent | 独立 user unit |
| Web restart | 只 kickstart Web | 只 restart Web unit |
| Worker stop | 结束并收敛其 Runner | unit cgroup 清理 Runner |

- Worker 必须是独立系统服务，不能是 Web 创建的线程或同一 systemd unit 内的后台进程。
- Ubuntu Worker unit 不配置随 Web unit 停止的 `PartOf` 等关系，并明确使用清理 Runner 的 cgroup 语义。
- macOS Worker 使用独立进程组监管 Runner；清理时结合任务 ID、进程创建身份和 Worker generation，不能
  只看 PID。
- 普通 Web 重启脚本不能附带 Worker stop、restart 或升级。
- Worker 升级使用独立维护流程：先原子进入 draining，拒绝新提交，再等待活动任务清空；不允许在“预检通过”和
  停止 Worker 之间接受新任务。服务定义变化后需要重新安装并分别验证 Web 与 Worker 健康状态。
- Web 与 Worker 健康信息同时暴露协议版本、Worker generation 和运行代码版本。涉及 Worker 协议或执行格式
  变更时，先进入 draining 并清空活动任务，再一次性更新 Web 与 Worker；不支持新 Web 管理旧 Worker、旧 Web
  管理新 Worker 或读取旧任务格式。版本不一致时只允许健康诊断并失败关闭新提交。
- `chub stop`、卸载和重新安装遇到活动 Worker 任务时默认拒绝，不自动取消。强制 Worker 停止必须是独立的
  明确维护操作，记录影响的任务并收敛为中断终态，不属于普通 Web 重启流程。

## 9. 失败边界

| 场景 | 产品语义 |
| --- | --- |
| Web 重启或崩溃 | Worker/Runner 继续；新 Web 恢复状态和结果 |
| Worker 不可用 | 新快速任务及全部 Session writer/生命周期变更操作返回 503，不回退本地执行 |
| Worker 崩溃 | 非最终任务中断并标记失败，不自动重放 |
| Runner/Codex 异常退出 | Worker 保存失败和有界错误信息 |
| 整机重启 | 非最终任务标记中断，不保证继续执行 |
| 记录损坏或权限异常 | 失败关闭，不猜测成功或空闲 |
| 协议不兼容 | 只允许健康诊断；新提交及依赖租约的 Session 写操作失败关闭，不尝试兼容旧任务格式 |
| 任务调用 Chub HTTP API | Web 重启窗口可能遇到短暂连接失败，不承诺调用无感 |

Worker 崩溃恢复不尝试接管旧 Runner。Ubuntu 依靠 Worker unit cgroup 清理；macOS 新 Worker 只清理能够
确认属于旧 Worker generation 的 Runner。无法确认 Runner 已结束时保持对应 Session 不可用，避免第二个
writer。

Worker 不可用或恢复屏障未完成时，由于 Web 无法安全识别“冲突 Session”，表中的限制实际按第 5.2 节
执行：全部 Session writer 和生命周期变更操作暂停，只读功能保持可用。

## 10. 分阶段实施

七个步骤是递进实施顺序，不是七个可以分别上线的产品版本。每一步都有明确的技术验证门槛，验证通过后
才能进入下一步；步骤一至五只完成开发与隔离验证，步骤六才进行正式一次性切换和产品验收，步骤七在核心
链路稳定后放宽旧重启限制。

步骤三至五涉及真实 Codex 执行时，统一使用临时数据目录、测试配置和专用测试 Session：

- 不读取、覆盖或迁移当前正式 Session 的运行状态；
- 不通过正式页面、微信 Hook 或生产任务入口启用 Worker 路径；
- 通知使用替身或明确的测试目标，不向真实微信收件人发送验收前消息；
- 测试服务、Socket、任务目录和 Session 使用独立标识，完成后按固定范围清理；
- 正式数据目录只在步骤六预检通过后启用。

### 步骤一：Worker 骨架与双平台服务

- 实现独立 Worker 进程、本机 IPC、协议版本和健康检查。
- 增加 macOS LaunchAgent 与 Ubuntu systemd user unit，并纳入安装、状态、停止和卸载流程。
- 健康与状态输出包含协议版本、Worker generation、运行代码版本和 `ready/draining` 状态。
- 暂不接管生产 Codex 任务，仅验证 Web 与 Worker 的服务生命周期相互独立。

本步验收：Web 与 Worker 能分别启动和报告健康；单独重启 Web 时 Worker 实例不变；Socket 权限正确；
Ubuntu 两个 user unit 不共享停止关系，macOS 两个 LaunchAgent 相互独立。

当前进展：代码、私有 IPC、健康协议、单实例锁和双平台服务定义已完成隔离自动化验证。2026-08-13 已在
macOS 安装并完成实机验收：Worker 为 `ready`，单独重启 Web 前后 generation 与 PID 保持不变，私有目录、
Socket、锁、服务日志目录和日志文件权限符合约束，两个 LaunchAgent 独立运行且无异常退出。Ubuntu 服务
定义已完成隔离验证，实机验收延后到代码同步至 Ubuntu 节点时执行，不阻塞 macOS 后续开发。

Ubuntu 后续同步验收固定按以下顺序执行，避免把安装成功或 Web 健康误当成 Worker 验收完成：

1. 等待节点现有快速交互与实时 Turn 结束，再同步同一份代码、依赖和非敏感配置。
2. 运行阶段一专项测试和 Shell 语法检查，随后执行 `./scripts/chub install`，确认两个 user unit 均已启用并运行。
3. 执行 `chub worker-health`，记录首次 generation 与 PID；检查协议版本、代码版本和 `ready` 状态。
4. 检查 Worker unit 不含随 Web 停止的 `PartOf` 关系并保持 `KillMode=control-group`；检查私有目录为 `700`，
   Socket、锁和相关日志文件为 `600`。
5. 执行一次 `chub restart` 并确认 Web 最终健康，再次执行 `chub worker-health`；generation 与 PID 必须和
   重启前一致。
6. 检查 Web 与 Worker 本轮日志、unit 退出状态和健康终态；没有异常重启、协议错误或权限退化后，才记录
   Ubuntu 阶段一实机验收通过。

阶段一当前没有其他功能遗留。macOS 已闭环；Ubuntu 只保留上述部署后实机验收项。Worker 尚未接管页面、
微信和翻译快速任务属于阶段一明确边界，不是遗留兼容路径。后续工作从步骤四开始；步骤四至步骤五仍是
隔离开发与验证，步骤六一次性删除 Web 内 Runner 并正式切换，不保留旧版本、旧任务格式或双轨回退。

### 步骤二：Worker 任务与 Runner 生命周期

- 实现每任务目录、完整私有输入、幂等提交与 tombstone、Runner 启动握手、查询、取消、绝对超时和原子完成记录。
- 使用固定测试任务验证执行协议，暂不连接页面、微信和真实 Session。
- 明确 Worker 崩溃、Runner 异常、损坏记录和超时的失败收敛，不自动重放。

本步验收：相同任务 ID 不启动第二个 Runner；取消和超时能确认进程组结束；任务接受与最终结果在进程退出
后仍可查询；Worker 重启不会把不确定任务重新执行。

当前进展：已完成每任务私有持久目录、固定大小请求与响应、带创建时间的任务 ID、规格摘要幂等校验、
tombstone、Runner 启动握手、进程创建身份、查询、列表、取消、绝对截止时间及原子完成记录。固定隔离
Runner 已覆盖成功、异常退出、强制取消、超时、最大多字节输入、状态损坏和 Worker 中断恢复；不确定任务
在新 Worker 启动时结束为失败且不会重放，Runner 进程组会按已保存身份清理。测试任务动作只在构造隔离
Worker 时显式启用，正式服务仍只开放健康检查，不读取或改写现有 Session，也不改变页面、微信和翻译路径。

步骤三开发前复核已同步修正步骤二边界：取消和超时必须确认整个 Runner 进程组结束，不能只等待组长进程；
任务目录、规格、状态、完成记录、tombstone 及 PID/创建时间会做交叉身份校验；任务协议升级为 v2，旧 Worker
只保留 v1 健康诊断而不能接受新任务；同时增加固定活动任务容量和任务目录上限，IPC 客户端可按操作使用有界
超时。服务状态命令在 Worker 健康失败时返回失败，避免只打印警告却保留成功退出码。

步骤二不需要维护者进行页面操作或真实消息验收，自动化隔离验收通过即可进入步骤三。正式环境中的 Worker
仍保持健康检查用途；跨 Web 重启、真实 Codex Session、通知恢复和页面交互分别留在步骤三至步骤六验收。

### 步骤三：Session 租约与真实 Codex 执行

- 接入真实 `codex exec`，实现 Session 租约和原生 writer 最终仲裁。
- 覆盖新建测试 Session 首次产生原生 Codex Session ID，以及该 Session 后续任务执行 `resume` 两条路径；
  不读取或迁移切换前已归档 Session 的运行态。
- 从可信事件持久化原生 Session ID；无法确认时保留任务结果并将 Session 失败关闭。

本步验收：同一 Session 不能并发两个 Turn；其他 Session 可并发；首次任务能可靠保存原生 Session ID；
检查与启动发生竞态时任务安全失败且不自动重试。

当前进展：已完成隔离 Codex 任务协议、每 Session 原子租约、固定工作区映射、原生 writer 启动前最终仲裁，
并从 `thread.started` 可信 JSONL 事件在执行中持久化原生 Session ID。首次任务无法确认 ID 时会保留最终消息
但将任务失败关闭；Worker 中断后保留已经观察到的 ID、结束不确定进程组并且不重放。自动化隔离回归已覆盖
同 Session 拒绝并发、不同 Session 并行、writer 占用、首次 ID、续接、重启恢复和 UUIDv7 事件格式。
2026-08-13 另使用临时 Git 工作区和只读权限完成真实 Codex CLI 验收：首次 `codex exec` 与随后
`codex exec resume` 均成功，两个 Turn 返回同一原生 Session ID。隔离目录已清理；专用测试 Session 按
Codex 原生能力归档。

第三阶段收尾复核又补齐三项失败边界：原生 Session 发现短暂缺失时不再移除仍有快速任务执行的 Chub
Session；任务接受写盘失败会回滚自己取得的 Session 租约；Worker 恢复 `resume` 任务时不会持久化与预期
不一致的原生 Session ID。相关专项回归通过。正式 Worker 仍只开放健康检查，页面、微信和翻译路径没有
切换；Ubuntu 完成前三步部署验收前不进入步骤四。

Ubuntu 同步前三步时，在步骤一的双服务验收之后继续完成以下检查：

1. 运行 Worker、Session 管理和服务脚本专项测试，确认固定任务、租约、崩溃恢复及 Ubuntu unit 回归通过。
2. 使用临时 Git 工作区、只读权限和专用测试 Session 执行一次真实 `codex exec`，再以其原生 Session ID
   执行一次 `codex exec resume`；两个 Turn 必须成功并返回同一 ID，完成后归档专用测试 Session。
3. 再次确认正式 Worker 为 `ready`、协议与代码版本为当前值，且测试任务和 Codex 任务开关均为关闭；页面、
   微信和翻译仍走原稳定路径，不在 Ubuntu 单独形成提前切流。
4. 检查 Web、Worker、操作日志和 Session 列表没有异常退出、残留 Runner、测试 Session 或错误 Busy 状态，
   再记录 Ubuntu 前三步验收通过并开始步骤四。

### 步骤四：统一迁移所有非实时 Codex 任务

- 完成页面快速交互、微信 Chub 任务和翻译任务对同一个 Worker 协议的适配，并只在隔离测试配置中启用。
- 翻译的有界顺序队列、排队超时、独立 Session 演进和执行启动整体迁入 Worker；`read-only` 和通知语义保持不变。
- 正式环境继续使用当前稳定路径直到步骤六；本步不得形成生产环境按任务类型分流。旧 Web Runner 的正式
  停用和删除放在步骤六一次性完成。

本步验收：三类任务都能提交、执行、取消或超时并生成现有格式的结果；进程树确认 Codex Runner 由 Worker
持有；隔离配置中 Worker 不可用时三类任务均明确失败，不出现某一类回退 Web 执行。

### 步骤五：Web 状态恢复与通知对账

- Web 启动时执行恢复屏障，恢复任务、Session 占用、原生 Session ID 和最终结果。
- 屏障完成后启动常驻增量对账，使没有打开页面的任务也能合并最终结果、日志和通知状态。
- 恢复页面、微信和翻译任务的通知状态；只接管 `pending`，不重放不确定的 `sending`。
- 保持现有页面 API 和消息时间线交互，不要求前端更换数据模型。
- 使用隔离服务完成 Web 重启端到端测试，不改变正式环境执行路径。

本步验收：执行中重启 Web 后任务继续，页面恢复为执行中并最终只出现一次结果；对应 Session 不短暂误报
空闲，其他 Session 可用；微信结果和通知状态不重复。

### 步骤六：一次性切换与新 Session 验收

- 维护者等待所有当前任务结束并手工归档已有 Session。
- 切换预检确认没有活动任务和未归档 Session，再安装正式 Worker 服务并启用正式执行路径。
- 一次性停用并删除 Web 内非实时 Codex Runner 的启动、关闭和自动回退路径；页面、微信和翻译全部切换，
  不保留按类型或配置回退的双轨模式。
- 按项目固定方式重启 Web，然后只使用新建 Session 进行验收；不迁移旧状态，不保留兼容模式。

本步验收：预检遇到活动任务或未归档 Session 时只拒绝切换，不自动处置；预检通过后新任务全部由 Worker
执行；既有归档历史仍可查看，但不会恢复为 Worker 任务。检查 Web 进程及其子进程，不存在由 Web 启动的
非实时 Codex Runner；停止 Worker 后，页面、微信和翻译提交均失败关闭。

### 步骤七：重启流程收敛

- 在跨 Web 重启恢复通过验收后，移除节点级“等待全部快速任务”和待重启提交门禁。
- 请求任务只等待自身结果与通知，其他普通 Worker 任务继续执行。
- 同步迁移翻译重启边界，并按“Chub 工作区 + 可写权限”的提交时固定规则加入 `restart_sensitive`
  保护，避免出现任务类型差异。
- 验证请求合并、重启结果通知和下一次待重启不会重复或漏记。

本步验收：任务 A 请求重启后，普通任务 B 不被中断且最终成功；任务 C 可在新 Web 恢复后提交；正在修改
Chub 运行资源并标记 `restart_sensitive` 的任务只延后自动重启，不形成节点级任务门禁。

步骤一至五验证失败时只回退对应开发或隔离测试改动，不影响当前正式执行路径。步骤六正式切换后不回退到
Web 内 Runner；故障时关闭新的非实时 Codex 提交并修复 Worker。在步骤五的 Worker 恢复链路和步骤六的
正式验收通过前，不删除当前延迟重启保护。

## 11. 验收标准

核心验收只看以下结果：

切换前先确认当前任务已结束、现有 Session 已归档；预检通过后按固定方式重启 Web，并只用新建测试
Session 验收。随后验证：

1. 快速交互执行中重启 Chub Web，Worker 和 Codex Runner 不退出，任务不中断。
2. 新 Web 恢复后，同一任务继续显示执行中并最终只产生一次结果。
3. 对应 Session 在恢复期间不误报为空闲、不允许第二个 Turn；任务结束后正确恢复为等待输入。
4. 原生 Codex Session ID 在首次快速交互跨重启创建时不丢失，首个任务不重复执行。
5. 其他空闲 Session 可继续提交任务，不存在节点级 `chub_restart_pending` 门禁。
6. 任务原绝对超时不重置，恢复后仍可取消。
7. 微信来源只收到一次完成结果，并在请求重启时收到一次新实例恢复结果；通知失败不改变任务结果。
8. 翻译任务在排队或执行中重启 Web 后继续由同一 Worker 管理，不重复提交，队列和通知状态正确恢复。
9. 页面快速交互、微信任务和翻译任务均由 Worker 执行；不存在仍由 Web 持有的非实时 Codex Runner。
10. 实时交互执行中重启 Web 后重新进入同一 Session，Codex Turn 保持现有不中断行为。
11. macOS 与 Ubuntu 均验证普通 Web 重启不停止 Worker、Runner 或 tmux。
12. Worker 崩溃、协议不兼容、状态损坏和整机重启均明确失败，不重复执行任务、不误报 Session 空闲；
    Worker 不可用或恢复未完成时，Session 写操作全部失败关闭。
13. 正式切换后停止 Worker，页面、微信和翻译提交全部明确失败；Web 进程不启动非实时 Codex Runner，
    不存在按任务类型或配置自动回退。
14. 无人打开页面时，常驻对账仍能合并任务结果、原生 Session ID、操作日志和通知状态；对账中断后
    能从已确认游标继续，不丢失或重复交付。
15. Worker 升级先进入 draining 并拒绝新提交；活动任务存在时，普通 stop、卸载和重新安装不会默认取消任务。

补充回归覆盖：Web 在 Worker 接受前、接受后、Runner 启动后、结果落盘后和通知处理期间重启；同一提交键
始终只关联一个任务，页面状态最终收敛，任务和通知各自保持既定语义。

## 12. 文档迁移

本文在方案评审和实施期间作为目标设计，不改写当前已验收行为。实现验收后：

- 更新 `README.md` 的快速交互和双服务安装说明。
- 将快速 Execution 所有权和恢复规则同步到 `AI_SESSION_STATE_DESIGN.md`。
- 将微信任务与通知恢复规则同步到 `CHUB_OPENCLAW_INTEGRATION_DESIGN.md`。
- 更新能力清单、OpenClaw 插件说明、配置示例和相关测试。
