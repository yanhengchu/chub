# Chub 前端 UI 模块化设计

> 状态：已验收。
> 主要读者：AI Agent、实现和排障 Agent；维护人员用于确认页面分层、交互边界和验收范围。
> 本文负责：Chub Web 前端分层、公共交互、固定资源加载顺序和 Standard/Cyber 视觉契约，遵循[Chub 总体架构](CHUB_ARCHITECTURE_DESIGN.md)。
> 本文不负责：后端 API、认证与安全契约、Quick Worker/OpenClaw 业务状态，以及已删除单体前端的拆分实施计划；这些内容由对应后端或专项文档维护。

## 1. 当前定位

Chub Web 继续使用 FastAPI、Jinja2、原生 JavaScript 和 CSS，同源部署且不引入前端构建链。首页前端按共享基础能力、通用组件、业务 Feature 和应用组合入口分层，目的是让每张动态卡片保持独立，同时维持轻量部署和跨平台行为。

当前结构已经取代单体 `app.js` / `app.css`。本文记录现行维护边界，不再作为拆分实施计划或阶段验收清单。

## 2. 当前目录与加载契约

```text
app/web/
  templates/
    index.html
    partials/app_styles.html
  static/
    app.js
    theme.js
    quick_interactions_core.js
    quick_interaction_session.js
    quick_interaction_timeline.js
    quick_interaction_conversation.js
    js/
      core/ai-usage.js
      core/dashboard-core.js
      components/collapsible-card.js
      components/ui.js
      features/node-status.js
      features/codex-sessions.js
      features/openclaw.js
      features/automations.js
      features/workstation.js
      features/project-documents.js
    css/
      tokens.css
      base.css
      components.css
      responsive.css
```

首页脚本仍使用全局函数和固定 `defer` 顺序，不是 ES Modules。加载顺序属于兼容契约：

```text
AI Usage Core -> Theme -> Dashboard Core -> Components -> Features -> app.js
```

- `ai-usage.js` 在 Theme 和首页脚本之前加载，统一管理跨页面额度请求、浏览器缓存、并发刷新和清理，并向消费者发布只读快照。
- `dashboard-core.js` 提供首页共享请求、认证、页面恢复和受控存储等基础能力。
- `components/` 只处理可复用 DOM 行为和无障碍状态，不包含业务请求。
- `features/` 分别拥有本功能的请求、缓存、轮询、状态和渲染。
- `theme.js` 只管理风格和 Cyber 代码雨；需要额度时订阅 Core 快照，不拥有额度请求或缓存。
- `app.js` 负责页面组合、启动、认证失效清理及确有必要的跨功能协调。
- 快速交互页按 `Quick Interaction Core -> Session View -> Timeline View -> Page Controller` 固定顺序加载。Core 管理请求与无状态业务规则；Session View 和 Timeline View 只消费只读快照、渲染 DOM 并通过回调上报操作；Page Controller 是 Session、任务列表、分页、轮询、提交和草稿状态的唯一所有者。
- 首页与快速交互页消费 Session API 返回的统一 `usage` 投影；Session 状态和入口规则以 [AI Session 状态模型](AI_SESSION_STATE_DESIGN.md) 为准。前端只负责按该投影展示入口、状态和刷新结果，不另行定义 owner、phase 或 Session 操作语义。
- 其他次级页面可继续使用独立脚本；只有形成稳定共享边界时才迁移公共能力。

调整文件或脚本顺序时，必须同步顺序测试并完成真实浏览器回归。

## 3. Feature 与卡片边界

首页当前包含节点状态、会话工作台（当前由 Codex Runtime 提供）、自动化任务、项目资料和工作站环境等 Feature。会话工作台统一管理实时终端和快速交互会话；其中实时终端与快速交互仍按各自的 Session 类型、状态和 writer 边界执行。自动化任务卡片将 `V 国内业务本期周报` 的资料下载与周报文档置于同一业务分区，但分别请求、渲染和报错，并各自标明所属周期：下载成功不代表重点清单或正式稿已生成，跨周在途下载也不覆盖新周期文档的周期标识；自动化环境只展示飞书登录与任务所需状态。工作站环境以“运行与维护”分组展示 `Chub`、`Chub Quick Worker`、`Chub Debug Chrome`、`OpenClaw Gateway` 和系统升级与恢复，并提供总刷新入口。各项分别拥有状态、操作、轮询和错误反馈，总刷新并行触发，不把单项失败扩散为整卡失败。OpenClaw Gateway 复用第三方 Gateway、消息通道和 Owner 状态，以综合状态和摘要展示；它提供受控重启和停止后启动，不提供停止或微信绑定入口。微信绑定统一在设置页“第三方服务 / OpenClaw · 微信 ClawBot”中完成；绑定只建立微信消息通道，不代替 Owner 配置或真实微信收发确认。首页不再展示低频日志卡片，完整日志页保留并由设置页“诊断”入口访问。

工作站环境中的 `Chub Debug Chrome` 行展示 Supervisor 服务和浏览器实例的分层状态：服务可用不代表实例已启动；实例仍按自动化任务需要启动。系统升级与恢复行的标题区只展示标题、状态描述和操作按钮；下方使用时间线展示请求记录、执行器启动、任务与写入停止、运行态清理、服务恢复和最终健康确认，并以紧凑状态项展示组件结果。`Chub Debug Chrome` 的 `succeeded` 只表示服务可用，`Debug Chrome 浏览器实例` 的 `skipped` 表示本次不纳入升级且不会自动启动。升级摘要显示当前/最近操作、更新时间和操作 ID；完整日志统一从设置页“日志详情”入口进入，首页不承载日志入口或完整日志。自动化任务卡片中的“飞书环境”使用独立边框信息组承载登录状态、检查操作、登录二维码和反馈消息；它不是独立服务，也不重复展示 Chub Debug Chrome 的生命周期状态。

每个动态能力遵循相同边界：

- 独立请求、缓存、刷新、轮询、渲染和报错；
- 刷新时保留最近一次成功内容，失败只提示本卡片；
- 页面历史返回时优先恢复安全的会话级缓存；
- 卡内操作只刷新真实受影响的卡片；
- Feature 不直接读取其他 Feature 的内部状态；共享资源操作由应用入口显式协调。
- 卡片内存在多个同级业务分区时，统一使用 `card-group-title` 作为分组标题，并以自然间距区分；不使用横线分割标题，也不在上一组最后一个项目后额外添加分割线。
- 环境类条目的状态标签使用短状态；未运行时，描述说明用途而不重复状态；可用时，描述展示连接方式、运行模式或校验日期；真实异常仍保留具体原因。

Chub、Chub Quick Worker、Ubuntu Chub Debug Chrome 与 OpenClaw Gateway 的普通重启彼此独立：它们分别由独立进程/服务承载，Chub 重启不会停止 Chub Quick Worker、Chub Debug Chrome 或 OpenClaw Gateway，Chub Quick Worker 重启也不会重启 Chub、Chub Debug Chrome 或 OpenClaw Gateway。Worker 重启是确认后的恢复操作，会取消排队任务、停止执行中任务并重建 Chub Quick Worker；它不等待 Worker 健康、任务空闲或 Chub 恢复，也不影响实时终端的 tmux 和原生 Codex。OpenClaw Gateway 的 API action `restart` 与微信 `restart clawbot` 都是“重启与恢复”入口：发现固定插件或补丁版本不一致时先同步，再重启 Gateway 并确认消息通道；当前 Gateway 停止、未知、未配置、通道异常或存在 Agent 任务不阻止恢复，目标版本、完整性或补丁锚点无法确认时失败关闭。页面按钮显示“重启与恢复”，API 和底层命令继续使用 `restart`，微信端使用 `restart clawbot`。微信固定维护指令为 `restart` / `restart web`、`restart worker`、`restart clawbot` 和 `upgrade`，均只接受精确无参数形式；受理与最终完成分别反馈。当前页面主动发起 Chub 重启、Chub Quick Worker 重启、OpenClaw Gateway 重启与恢复或系统升级与恢复时，只有在新 Chub 实例健康、与本页请求 operation ID 匹配的 Worker 重启成功、OpenClaw Gateway/通道及兼容基线恢复成功或本次升级 operation 成功后，先保留成功状态约 2 秒，再自动整页刷新一次；历史成功记录和失败终态不触发刷新。确认弹窗必须明确 OpenClaw Gateway 会短暂中断消息通道和 Agent 任务，Chub Quick Worker 任务会被取消/停止且不自动重试。系统升级与恢复是例外：它独占受影响的 Chub AI Runtime 写入，统一停止任务、清理 Chub Session 关联与 Worker 运行态并切换 Chub/Chub Quick Worker；启动门禁只校验固定脚本、服务定义和运行态路径，不检查当前 Chub/Chub Quick Worker 状态。升级方案无法读取或校验时，按钮降级为当前版本运行态恢复，并明确不执行代码版本升级；确认信息无法取得任务数量时，不显示为 0，而是提示按固定边界清理。确认弹窗按当前状态简要列出受影响的快速任务数、Chub Session 关联数和服务切换范围；升级完成后组件摘要明确区分 Chub Debug Chrome 服务已确认与浏览器实例未纳入升级（不会自动启动）。Codex 原生 Session、配置和业务资料不因该操作删除。系统升级进行中禁用 Chub、Chub Quick Worker、OpenClaw Gateway 重启与恢复及升级恢复的重复操作；失败终态释放这些入口，仍保留各自的固定目标和结果预检。重启或绑定后的状态不能代替维护者在微信客户端进行的实际收发确认。

不建立全局业务状态仓库，也不通过提示文案反推业务状态。API 路径、统一响应、认证和安全边界由后端契约决定，前端分层不能改变这些语义。

## 4. 公共组件与交互

公共组件覆盖稳定、可复用的交互，包括折叠卡片、状态标签、通用提示、错误反馈和确认弹窗。公共确认组件负责一致的模态结构、遮罩、忙碌态、关闭规则与错误展示；具体标题、影响说明、按钮语义和业务动作仍由所属 Feature 或应用入口管理。

当前公共交互规则为：

- 动态内容通过 DOM API 和 `textContent` 渲染，不使用 `innerHTML` 注入外部内容；
- 业务确认、选择和编辑使用 `showModal()` 打开的原生 `dialog`，不使用浏览器默认 `confirm()` 或 `alert()`；
- 确认弹窗统一包含标题、对象与影响说明、反馈区、取消和确认操作；危险操作使用危险按钮并说明恢复边界；
- 提交期间锁定关闭和重复操作；同步失败留在弹窗展示，已受理异步操作的进度与最终失败回到所属卡片或全局反馈区；
- 下级页面在当前标签打开并依赖浏览器返回，不额外增加专用返回栏；
- 卡片标题负责折叠，内部按钮、链接和表单控件不得误触发；
- 折叠偏好保存在浏览器本地，存储不可用时回退页面内存；
- 键盘、焦点、`aria-expanded` 和系统“减少动态效果”必须保持有效；
- 桌面双列和手机单列均使用卡片自然高度，不强制同排等高。

确认弹窗在桌面端居中显示，手机端使用底部抽屉；空闲时可通过关闭按钮、取消、遮罩或 Escape 退出。首页、项目资料页和快速交互页共享同一视觉与状态语义，不能因入口不同退回浏览器默认弹窗。

## 5. 样式分层

公共页面样式通过 `templates/partials/app_styles.html` 统一加载，固定顺序为：

```text
tokens.css -> base.css -> components.css -> responsive.css
```

- `tokens.css` 当前只保存共享颜色和页面间距变量；圆角、阴影、动效及大部分组件尺寸仍由所属样式规则直接定义。
- `base.css` 保存页面基础排版、原生控件和全局交互规则。
- `components.css` 保存公共组件及尚未在真实改动中进一步归类的页面规则。
- `responsive.css` 保存桌面和手机端响应式调整。

已删除的 `static/app.css` 不再恢复。后续只在真实页面改动时渐进归类规则，避免为了目录整齐改变 CSS 层叠结果。

## 6. Standard 与 Cyber

`Standard` 是默认且长期维护的简约标准版：强调清晰层级、克制颜色与装饰、自然高度卡片、统一间距和轻量动效。新增页面和功能默认先复用 Standard 的 Token、组件和交互模式。

`Cyber` 是已验收的科技终端风格。它与 Standard 共用模板语义、Feature 逻辑、接口、安全边界、键盘操作和响应式结构，仅通过主题标识、Token 和公共组件表达深色终端视觉。

两套风格的共同约束包括：

- 公共页面桌面端最大内容宽度为 1080px，手机端按可用宽度自然铺满；
- Android Chrome 属于当前移动浏览器验收范围；固定覆盖 `360x800` 和 `412x915` 两个 Android 常见视口，并额外检查软键盘打开后的快速交互输入区、全面屏安全区和横向无溢出。Chub 不承诺原生 Android 应用、离线 PWA 或后台推送能力；移动访问需要可信 Tailnet 连接。
- 普通文字按钮和按钮型导航使用统一的 14px 字号及相同主次语义；
- 风格选择和 Cyber 代码雨参数保存在当前浏览器，读取失败时回退 Standard；
- 非敏感主题 Cookie 用于服务端首屏设置风格和 `color-scheme`，不得因此放宽 CSP；
- Cyber 代码雨仅作背景，不拦截交互；减少动态效果时停止流动；
- Cyber 代码雨随机使用 `0–1` 和由点或空格连接的本地小写短语库；短语覆盖日常、开发和沟通语境，运行时不依赖外部网络。额度雨列只消费共享 AI Usage 快照，将 `weekly`、`today` 显示为单字符垂直雨列，最左轨道持续展示 `weekly`，最右轨道持续展示 `today`；Codex 额度来源、缓存、认证失败和不可用规则由[Codex AI 额度与用量采集设计](CODEX_AI_QUOTA_USAGE_DESIGN.md)维护；
- 状态不能只依赖颜色表达，危险操作仍需明确文案和确认；
- 终端 PTY 保持独立的原生终端样式，不参与公共页面主题切换。

主题预览只展示静态示例和本地交互，不读取真实节点、任务或会话数据，也不执行维护操作。

## 7. 维护规则

新增或调整首页功能时：

1. 请求、状态、缓存和渲染放入对应 Feature；共享请求和页面生命周期放入 Core。
2. 仅在多个真实功能稳定复用时新增 Component，不为目录形式引入空泛抽象。
3. `app.js` 只保留组合、启动和跨功能协调，不重新堆积业务实现。
4. 同步检查 Standard、Cyber、桌面、Android Chrome 手机视口、键盘和减少动态效果。
5. 保持固定脚本和样式顺序；涉及公共层时运行顺序测试和浏览器回归。

当前结构已覆盖首页实际能力，不需要引入前端框架、全局事件总线、打包器或独立状态管理库。若未来出现多页面复用或复杂生命周期，再从真实调用中提取最小接口。

## 8. 验收范围与复检

- 已验证范围：首页固定脚本/样式加载顺序、Core/Components/Feature 边界、动态卡片独立状态、确认弹窗与折叠交互、Standard/Cyber 主题，以及受管桌面 Chrome 模拟的 `360x800`、`412x915` 手机视口、键盘和减少动态效果页面契约。
- 未验证或不承诺：真实 Android 设备、未在本次回归中实际运行的浏览器、平台或新增页面；本文不替代后端 API、安全边界和各业务专项的最终状态验收。
- 复检触发：脚本或样式加载顺序、公共组件语义、页面状态投影、Standard/Cyber Token、响应式布局、键盘/无障碍行为或浏览器存储契约变化时，必须同步运行顺序测试并完成受影响桌面和手机视口的真实浏览器回归。
