# Chub 能力编排外置架构设计

> 状态：调研中
> 主要读者：需要设计、实现或排障能力编排模块的 AI Agent；维护人员用于确认通用架构、状态归属和模块维护边界。
> 本文负责：定义能力编排模块的通用职责、Chub 进程内能力执行面、请求与检查点模型、模块协议、生命周期和平台验收原则。
> 本文不负责：任何具体入口或业务任务的编排逻辑、用户可见交互和分流开关；微信普通正文任务由[Chub 微信任务编排外置设计](WEIXIN_TASK_ORCHESTRATION_EXTERNALIZATION_DESIGN.md)维护。
> 维护说明：Runtime ZIP 外置已是当前实现；能力编排外置尚未实现。本设计只定义所有后续编排模块共用的架构，不将其表述为当前可调用能力。

## AI 可执行契约

1. 能力编排外置的是**任务流程控制权**。模块决定在已授权上下文中如何组织步骤；Chub 继续拥有入口校验、权限、实际执行、任务终态、通知、日志和模块维护。
2. 模块与 Chub 同机，由 Chub 从固定目录加载。Chub 的**进程内能力执行面**不是远程服务或通用 HTTP API；它复用 Chub 现有的 Session、任务、Worker、Runtime 和通知能力，保证其状态所有权不被模块绕过。
3. 模块只能查询并调用本次编排请求获得授权的能力投影。能力目录以[Chub 集成能力清单](CHUB_INTEGRATION_CAPABILITIES.md#11-核心能力)为准；能力 ID 不是模块可直接导入的函数。模块不得操作 Worker、Runtime、微信、文件、网络或系统命令，也不得提供 Runtime、原生 Session、路径、命令、URL、环境变量、收件人或身份材料。
4. Chub 为每次编排保存 `orchestration_id`、入口幂等关联、规范化上下文、不可变模块实现引用、已授权能力、能力调用记录、检查点和最终状态。模块只定义检查点语义，不能直接读写 Chub 持久化状态。
5. 能力执行面只返回 `succeeded`、`waiting`、`rejected`、`unavailable` 或 `unknown`。模块只能依据可信成功或对应等待关联的终态推进；不得以 HTTP 成功、进程启动、日志文本或模型正文推断成功。
6. 模块在一个事件回合中至多请求一次能力，随后返回检查点、等待声明或 `workflow.complete`。它不能以未受控线程、计时器、连接或内存会话推进流程。`workflow.complete` 只可返回 `completed`、`discarded` 或 `failed` 及有界非敏感结果投影；Chub 决定是否及如何通知。

## 通用架构与状态

```text
受保护入口完成认证、幂等和输入校验
        |
        v
Chub 编排协调层：创建请求并按范围选择路径
  ├─ `internal`：内置流程 → Chub 进程内能力执行面
  └─ 开发或已激活模块：模块决定下一步并返回检查点
                              |
                              v
                    Chub 进程内能力执行面：校验、提交、等待并保存可信结果
                              |
                              v
                    Chub / Runtime / Quick Worker：实际执行并维护领域终态
```

编排协调层拥有编排请求、能力调用记录、检查点、活动偏好和最终状态。Worker 不加载或重新解释编排模块；它只执行由 Chub 提交的受控任务。模块在 Web 控制面收到调用或可信能力完成事件后进入下一回合。

编排逻辑状态固定为 `accepted`、`running`、`waiting_capability`、`resuming`、`completed`、`discarded` 或 `failed`。Worker 任务状态、Session 生命周期、Native 映射和通知终态继续分别以其专项设计为准。重复入口消息只读取首次请求的当前决定或终态，不重新创建副作用。

重启后，Chub 先对账已提交能力的真实结果：可确认则按原检查点和原模块产物恢复；未完成则继续等待；结果未知且继续会重复副作用时明确失败。不得切换到当前活动模块、内置流程、另一 Runtime 或另一 Session 来“恢复”已受理请求。

## 模块协议与生命周期

正式或已导入的模块包固定为 ZIP，包根目录有唯一的 `chub-capability-orchestration.json`，且 `module_type` 固定为 `capability-orchestration`。它使用独立安装目录、暂存目录、注册表和恢复记录，不能复用 Runtime 模块的目录、槽位、加载器或状态清理记录。

在 ZIP 生命周期实现前，某个具体编排任务可使用 Chub 固定加载的**开发实现**完成首轮分流验证。具体任务必须声明其稳定的开发实现名，例如微信普通正文的 `weixin-orchestration-dev`；它在活动偏好中以 `development_ref` 表示，只从仓库内的固定开发目录加载，不接受页面、微信、模块或配置传入的代码路径。开发实现不等同于 `internal` 内置流程，也不提供任意模块发现或安装。

每次加载开发实现都计算源码摘要，并将“开发实现名 + 源码摘要”保存到已受理请求。存在非终态请求时不得重载其开发目录；Web 重启后发现已保存摘要与当前源码不一致时，相关请求明确失败，不得以新源码继续旧检查点。后续导入的 ZIP 使用独立、不可变的 `implementation_ref`。

清单至少声明 `protocol_version`、`module_id`、`version`、`scope`、`orchestration_protocol_version`、`capability_protocol_version`、`display_name`、`description`、`chub_version` 和 `entry`。未知协议、无效入口、超出声明范围、输入/检查点模型不兼容或能力执行面协议不支持时，候选必须保持未激活。

Chub 为每个导入包计算不可变 `implementation_ref`（`module_id@version` 加内容摘要）。活动偏好以 `scope -> internal | development_ref | implementation_ref` 保存；已受理请求固定到内置路径、带源码摘要的开发实现或不可变 ZIP 产物。相同版本的新包也是新产物，不能覆盖或删除仍被非终态请求引用的旧产物。停用只影响后续新请求；已受理请求继续原路径或模块产物，不能自动改投或回退。

| 活动偏好值 | 含义 | 已受理请求绑定 |
| --- | --- | --- |
| `internal` | Chub 内置流程，不委托编排模块 | 内置路径与既有恢复规则 |
| `development_ref` | 具体任务的固定开发实现名加源码摘要 | 相同开发实现与摘要；源码变化不得继续旧检查点 |
| `implementation_ref` | 已导入 ZIP 的模块标识、版本和内容摘要 | 相同 ZIP 产物 |

导入、激活、停用、替换、移除和恢复均记录 `requested`、`started`、`succeeded` 或 `failed`。目录写入、入口加载或 HTTP 成功不代表操作成功；必须确认注册表、活动偏好和受影响请求的最终状态。模块专属缓存可按固定白名单清理，Chub 通用设置、编排记录、Session、Worker 任务、通知和第三方数据不得清理。

## 通用接入与验收

新增具体编排任务前，必须先定义其入口上下文、允许能力、模块范围、`internal`/开发实现/ZIP 实现的启用分流规则、开发实现名与源码摘要规则、检查点语义、用户可见终态和失败边界。具体设计不得扩大为全局任务执行器，也不得把能力执行面暴露为任意 HTTP 调用入口。

所有模块至少验证：能力越权与伪造引用被拒绝；重复入口不重复执行；能力结果未知不产生替代副作用；重启、模块回合中断、停用、替换和移除不改写已受理请求；模块安装与激活的最终状态可确认。真实外部通道的收发仍由维护者在对应客户端完成验收。

## 相关文档

- [Chub 微信任务编排外置设计](WEIXIN_TASK_ORCHESTRATION_EXTERNALIZATION_DESIGN.md)：首个具体编排任务的分流、流程和阶段验收。
- [Chub 集成能力清单](CHUB_INTEGRATION_CAPABILITIES.md)：能力语义、当前状态和固定指令契约。
- [Chub 总体架构设计](CHUB_ARCHITECTURE_DESIGN.md)：分层和状态所有权。
- [Chub AI Runtime 外置模块功能设计](CHUB_EXTERNAL_MODULE_DESIGN.md)：Runtime 模块的独立外置机制。
- [Chub Quick Worker 独立服务设计](CHUB_QUICK_WORKER_DESIGN.md)：任务、恢复和通知终态。
