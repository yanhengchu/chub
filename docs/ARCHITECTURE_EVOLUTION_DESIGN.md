# Chub 前端 UI 模块化设计

> 状态：已实现并验收。当前首页采用固定加载顺序的原生 JavaScript/CSS 分层，Standard 与 Cyber 共用同一套业务结构和交互语义。

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
    js/
      core/dashboard-core.js
      components/collapsible-card.js
      components/ui.js
      features/node-status.js
      features/codex-sessions.js
      features/openclaw.js
      features/automations.js
      features/project-documents.js
      features/logs.js
      app.js
    css/
      tokens.css
      base.css
      components.css
      responsive.css
```

首页脚本仍使用全局函数和固定 `defer` 顺序，不是 ES Modules。加载顺序属于兼容契约：

```text
Core -> Components -> Features -> app.js
```

- `dashboard-core.js` 提供共享请求、认证、页面恢复和受控存储等基础能力。
- `components/` 只处理可复用 DOM 行为和无障碍状态，不包含业务请求。
- `features/` 分别拥有本功能的请求、缓存、轮询、状态和渲染。
- `app.js` 负责页面组合、启动、认证失效清理及确有必要的跨功能协调。
- 次级页面可继续使用独立脚本；只有形成稳定共享边界时才迁移公共能力。

调整文件或脚本顺序时，必须同步顺序测试并完成真实浏览器回归。

## 3. Feature 与卡片边界

首页当前包含节点状态、Codex 会话、OpenClaw、自动化任务、项目资料和日志等 Feature。每个动态卡片遵循相同边界：

- 独立请求、缓存、刷新、轮询、渲染和报错；
- 刷新时保留最近一次成功内容，失败只提示本卡片；
- 页面历史返回时优先恢复安全的会话级缓存；
- 卡内操作只刷新真实受影响的卡片；
- Feature 不直接读取其他 Feature 的内部状态；共享资源操作由应用入口显式协调。

不建立全局业务状态仓库，也不通过提示文案反推业务状态。API 路径、统一响应、认证和安全边界由后端契约决定，前端分层不能改变这些语义。

## 4. 公共组件与交互

公共组件覆盖稳定、可复用的交互，包括折叠卡片、状态标签、通用提示和错误反馈。业务差异明显的确认弹窗继续由所属 Feature 或应用入口管理。

当前公共交互规则为：

- 动态内容通过 DOM API 和 `textContent` 渲染，不使用 `innerHTML` 注入外部内容；
- 业务确认、选择和编辑使用原生 `dialog`，并提供明确关闭入口和影响说明；
- 下级页面在当前标签打开并依赖浏览器返回，不额外增加专用返回栏；
- 卡片标题负责折叠，内部按钮、链接和表单控件不得误触发；
- 折叠偏好保存在浏览器本地，存储不可用时回退页面内存；
- 键盘、焦点、`aria-expanded` 和系统“减少动态效果”必须保持有效；
- 桌面双列和手机单列均使用卡片自然高度，不强制同排等高。

## 5. 样式分层

公共页面样式通过 `templates/partials/app_styles.html` 统一加载，固定顺序为：

```text
tokens.css -> base.css -> components.css -> responsive.css
```

- `tokens.css` 保存颜色、字号、间距、圆角、阴影和动效等设计变量。
- `base.css` 保存页面基础排版、原生控件和全局交互规则。
- `components.css` 保存公共组件及尚未在真实改动中进一步归类的页面规则。
- `responsive.css` 保存桌面和手机端响应式调整。

已删除的 `static/app.css` 不再恢复。后续只在真实页面改动时渐进归类规则，避免为了目录整齐改变 CSS 层叠结果。

## 6. Standard 与 Cyber

`Standard` 是默认且长期维护的简约标准版：强调清晰层级、克制颜色与装饰、自然高度卡片、统一间距和轻量动效。新增页面和功能默认先复用 Standard 的 Token、组件和交互模式。

`Cyber` 是已验收的科技终端风格。它与 Standard 共用模板语义、Feature 逻辑、接口、安全边界、键盘操作和响应式结构，仅通过主题标识、Token 和公共组件表达深色终端视觉。

两套风格的共同约束包括：

- 公共页面桌面端最大内容宽度为 1080px，手机端按可用宽度自然铺满；
- 普通文字按钮和按钮型导航使用统一的 14px 字号及相同主次语义；
- 风格选择和 Cyber 代码雨参数保存在当前浏览器，读取失败时回退 Standard；
- 非敏感主题 Cookie 用于服务端首屏设置风格和 `color-scheme`，不得因此放宽 CSP；
- Cyber 代码雨仅作背景，不拦截交互；减少动态效果时停止流动；
- 状态不能只依赖颜色表达，危险操作仍需明确文案和确认；
- 终端 PTY 保持独立的原生终端样式，不参与公共页面主题切换。

主题预览只展示静态示例和本地交互，不读取真实节点、任务或会话数据，也不执行维护操作。

## 7. 维护规则

新增或调整首页功能时：

1. 请求、状态、缓存和渲染放入对应 Feature；共享请求和页面生命周期放入 Core。
2. 仅在多个真实功能稳定复用时新增 Component，不为目录形式引入空泛抽象。
3. `app.js` 只保留组合、启动和跨功能协调，不重新堆积业务实现。
4. 同步检查 Standard、Cyber、桌面、手机、键盘和减少动态效果。
5. 保持固定脚本和样式顺序；涉及公共层时运行顺序测试和浏览器回归。

当前结构已覆盖首页实际能力，不需要引入前端框架、全局事件总线、打包器或独立状态管理库。若未来出现多页面复用或复杂生命周期，再从真实调用中提取最小接口。
