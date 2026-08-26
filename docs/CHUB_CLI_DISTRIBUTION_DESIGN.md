# Chub CLI 分发、安装与发布设计

> 状态：待实现
> 主要读者：在新设备上安装 Chub、配置 ClawBot 或发布新版本的维护者；AI Agent 用本文执行安装和发布操作。
> 本文负责：给出安装、启动、使用、升级和发布的具体动作与预期结果。
> 本文不负责：解释 Web、Quick Worker、AI Runtime、Session、OpenClaw 协议和微信安全边界的内部实现；这些内容以相关架构、模块和[能力清单](CHUB_INTEGRATION_CAPABILITIES.md)为准。
> 维护说明：本文只描述正式 npm 交付路径。Chub 主发行包和 Chub OpenClaw 插件通过 npm 发布；GitHub Release 只负责版本说明和归档，不提供替代安装包。OpenClaw 和微信通道按各自官方渠道安装。这里的“主发行包”是安装包范围，不等同于三层架构中的 Chub 核心层。
> 当前阶段：设计优化。本文中的包名、命令和版本号是目标交付契约；在“实施准入与待确认决策”完成前，不把它们当作当前可执行安装指令。

## 操作目标

维护者只需要记住两条用户路径：

| 路径 | 目标 |
| --- | --- |
| Chub 主发行包 | 安装 CLI，启动 Chub，在 Web 中进行快速交互 |
| 可选 ClawBot | 安装 OpenClaw/微信集成，在微信中进行交互 |

Quick Worker 随 Chub 主发行包一起安装和启动，不需要单独安装或学习。只有排障时才查看它的状态。

本文所有操作都遵循三条判断：

1. 命令执行后必须确认目标结果，不把“命令已运行”当作成功。
2. 安装或升级不得覆盖用户配置、Token、日志和第三方数据。
3. 失败时停止后续动作，说明原因和下一步恢复动作。

## 1. 安装 Chub 主发行包

### 1.1 目标新设备安装流程

正式 npm 包发布并完成兼容性验收后，Release notes 必须先给出本版本支持的 Node.js/npm、OpenClaw 和腾讯微信插件版本。维护者在 macOS 或 Ubuntu 新设备上执行：

```bash
npm install -g chub
chub help
chub start
```

结果应为：

- `chub help` 可以在服务未启动时显示安装、启动和常用命令。
- 首次 `chub start` 检测到服务未安装时，自动完成必要配置、用户服务安装和 Web/Quick Worker 启动；重复执行只启动并检查现有服务，不覆盖用户配置。
- `chub start` 输出 Web 访问地址，并确认 Web 与 Quick Worker 已达到可用状态。
- Chub 内部所需的 Quick Worker 由同一命令自动处理；用户不执行单独的 Worker 安装命令。

如果 `chub start` 报告缺少 Node.js/npm、Python、Codex、`ttyd`、`tmux`、端口或权限，按 Release notes 的平台前置条件处理后重新执行；不要绕过 CLI 手工修改服务文件。首次引导、服务安装或健康检查失败时，发布不得标记为可用。

### 1.2 启动后的 Web 验收

1. 打开 `chub start` 输出的 Web 地址。
2. 使用真实 loopback 或受信任的 Tailnet 访问。
3. 新建 Codex Session，选择快速交互。
4. 提交一条低风险测试任务。
5. 确认页面显示任务受理、执行状态和最终结果。

正常使用不需要执行 `chub worker-health`。维护者需要一次性做完整只读诊断时可运行 `chub check`，它检查项目配置、两个服务、Web、Quick Worker 和系统状态，不执行重启。页面任务无法推进时，再按[Quick Worker 设计](CHUB_QUICK_WORKER_DESIGN.md)使用 `chub status`、`chub worker-health` 或 `chub worker-reload` 排查和恢复；`chub worker-reload` 本身允许在 Worker 忙碌、协议不兼容或不可达时重建服务并清理 Worker 任务。`chub worker-recover` 仍保留为本机终端的直接服务恢复入口。

## 2. 安装和使用 ClawBot

ClawBot 不是本仓库中的一个独立程序。完整链路由以下三部分组成：

| 部分 | 来源 | 本仓库是否提供源码/安装包 |
| --- | --- | --- |
| OpenClaw Gateway | OpenClaw 官方项目 | 否，必须按 OpenClaw 官方文档安装 |
| 微信通道/ClawBot 插件 | 第三方腾讯微信插件项目 | 否，必须按该项目自己的版本说明安装 |
| Chub OpenClaw 插件与 Hook | 本仓库 `integrations/openclaw/chub/` | 是，只提供 Chub 侧适配和固定路由 |

OpenClaw 侧定制只保留第三方微信适配器的可信语音字段、Context Token 持久化和日志脱敏补丁，针对实际运行目录，不属于 Chub OpenClaw 插件；普通文本原文首尾空格不属于兼容承诺。安装顺序必须保持为“OpenClaw -> 微信通道/ClawBot -> Chub 插件 -> 适配器最小补丁复检 -> 微信实测”。具体边界见[OpenClaw 定制集成设计](OPENCLAW_CUSTOMIZATION_DESIGN.md)第 6、7 节。

### 2.1 安装 OpenClaw

在新设备上使用 Release notes 指定的 OpenClaw 版本和官方安装方式。npm 路径示例为：

```bash
npm install -g openclaw@X.Y.Z --allow-scripts=openclaw
openclaw onboard --install-daemon
openclaw --version
openclaw gateway status
```

不同 OpenClaw 版本或平台的参数可能变化；以[官方安装文档](https://github.com/openclaw/openclaw/blob/main/docs/install/index.md)为准，不要使用未在 Release notes 验证的 `latest`。完成后 `openclaw --version` 必须与兼容性矩阵一致，并且 Gateway 已安装并处于可启动/运行状态。

### 2.2 安装微信通道/ClawBot

当前 OpenClaw Weixin 通道的参考 npm 包为 `@tencent-weixin/openclaw-weixin`；正式支持的包名、版本和兼容矩阵必须在实施前锁定，并写入 Release notes。

1. 安装并启用微信通道：

   ```bash
   openclaw plugins install npm:@tencent-weixin/openclaw-weixin@X.Y.Z --pin
   openclaw config set plugins.entries.openclaw-weixin.enabled true --strict-json
   openclaw gateway restart
   ```

2. 在运行 Gateway 的同一台设备启动二维码登录并绑定微信账号：

   ```bash
   openclaw channels login --channel openclaw-weixin
   ```

   由维护者本人使用微信扫描并确认登录。

3. 如通道要求发送者配对，查看并批准唯一 Owner：

   ```bash
   openclaw pairing list openclaw-weixin
   openclaw pairing approve openclaw-weixin <CODE>
   ```

4. 启动或确认 OpenClaw Gateway，执行：

   ```bash
   openclaw config validate
   openclaw plugins inspect openclaw-weixin --runtime --json
   openclaw channels status --probe --json
   ```

5. 确认插件为 `loaded`、目标微信账号为 `running`，并确认 Owner/配对状态满足当前微信 Chub 模式要求，再继续安装 Chub 插件。微信插件源码和账号数据仍由腾讯微信项目维护，Chub 不代发或替代它。

### 2.3 安装 Chub 插件与 Hook

建议将 Chub OpenClaw 插件和 Hook 作为独立 npm 包 `@chub/openclaw-plugin` 发布。它只安装 Chub 适配插件，不会安装 OpenClaw 或微信通道；用户直接安装该 npm 包：

```bash
openclaw plugins install npm:@chub/openclaw-plugin@X.Y.Z --pin
openclaw gateway restart
openclaw plugins inspect chub --runtime --json
```

首次配置时，将地址替换为运行 Chub 节点的实际 Tailnet 地址和端口：

```bash
openclaw config set plugins.entries.chub.enabled true --strict-json
openclaw config set plugins.entries.chub.config.baseUrl '"http://<CHUB_TAILSCALE_IP>:<PORT>"' --strict-json
openclaw config set plugins.entries.chub.config.weixinChubMode true --strict-json
openclaw config validate
openclaw gateway restart
openclaw plugins inspect chub --runtime --json
openclaw channels status --probe --json
```

Chub 节点自身还必须启用微信 Chub 模式，修改后执行 `chub restart`。检查结果应同时满足：Chub 地址可达、Chub 插件为 `loaded`、微信通道为 `running`。部署副本只能由 `openclaw plugins install npm:...` 从已发布的 npm 包生成，不直接编辑扩展目录。核心 `chub` 与 `@chub/openclaw-plugin` 应使用同一兼容版本；具体版本以 Release notes 为准。

### 2.4 复检 OpenClaw 最小定制

可信语音字段和 Context Token 由第三方腾讯微信插件实际运行目录承载；Chub 只提供最小兼容规则和复检方法。首次安装、微信插件升级、重装、运行目录变化，或已确认出现语音/Token/出站异常时，都必须执行下面的复检；底层 Gateway 重启本身不自动打补丁，首页“重启与恢复”会在固定目标版本可确认时执行受控同步。

1. 使用 `openclaw plugins inspect openclaw-weixin --runtime --json` 找到实际加载的微信插件版本和运行目录。
2. 按[OpenClaw 定制集成设计第 7 节](OPENCLAW_CUSTOMIZATION_DESIGN.md#7-版本部署与复检流程)检查 `accountId + userId` 持久化、启动恢复、懒恢复和文件权限 `600`。
3. 如果功能缺失，先做版本和适用性检查，再只应用可信语音或 Context Token 对应的最小变更；上游已等价实现、版本不匹配或检查失败时不得强行应用，也不得直接修改 Chub 插件源码来替代适配器补丁。首页“重启与恢复”会对已锁定的目标版本执行同样的固定同步；底层 `openclaw gateway restart` 仍只是低层重启命令。
4. 重启 Gateway，重新检查插件加载、Gateway 健康、通道状态和 Token 文件权限。每次微信插件更新后都必须重复复检；具体补丁锚点和恢复边界只维护在[OpenClaw 定制集成设计](OPENCLAW_CUSTOMIZATION_DESIGN.md)中。日志和命令输出不得暴露 Token 或完整收件人信息。

### 2.5 微信验收

以下动作必须由维护者本人在真实微信客户端完成，编码 Agent 不能代操作：

1. 启动 OpenClaw Gateway，启动并绑定目标微信账号。
2. 确认 Gateway、微信通道、Chub 插件和 Chub 地址均处于可用状态。
3. 在微信中发送 `chub` 或 `help`，确认收到正确的即时回复。
4. 再发送一条低风险文字任务；如需验证语音，再发送一条真实语音。
5. 确认微信收到任务受理/同步回复和最终结果，并与 Chub 任务终态、通知终态一致。

Agent 只能辅助检查 Gateway/Chub 日志、任务状态和通知终态；这些后台记录不能代替维护者确认微信实际收发结果。插件身份、路由、协议和失败边界见 [OpenClaw 定制集成设计](OPENCLAW_CUSTOMIZATION_DESIGN.md) 与[插件说明](../integrations/openclaw/chub/README.md)。

## 3. `chub help` 和其他命令

`chub help` 是安装后的第一条检查命令，必须包含：

```text
Chub CLI

Install:
  npm install -g chub
  chub start

Use:
  打开 chub start 输出的 Web 地址，进行快速交互。
  需要微信交互时，单独安装和配置 ClawBot。
```

状态、日志、Worker 排障、通知、需求储备和其他命令不在本文展开，统一查看[集成能力清单](CHUB_INTEGRATION_CAPABILITIES.md)。

## 4. 升级和回滚

### 4.1 Chub 主发行包

升级到指定版本：

```bash
npm install -g chub@X.Y.Z
chub start
```

确认 Web 可以访问、快速交互可提交并返回最终结果后，才认为升级完成。回滚使用已验证的旧版本：

```bash
npm install -g chub@PREVIOUS_VERSION
chub start
```

配置和运行数据应保留；协议或运行态变化时按对应专项文档执行维护和恢复，不由 npm 安装脚本静默清理。

### 4.2 ClawBot

升级时分别处理第三方微信通道和 Chub OpenClaw 插件，并使用 Release notes 固定的精确版本：

```bash
openclaw plugins update npm:@tencent-weixin/openclaw-weixin@X.Y.Z
openclaw plugins update npm:@chub/openclaw-plugin@X.Y.Z
openclaw gateway restart
openclaw plugins inspect chub --runtime --json
openclaw plugins inspect openclaw-weixin --runtime --json
openclaw channels status --probe --json
```

升级后必须重新执行 Context Token 复检，并由维护者确认实际微信结果。Chub 主发行包、Chub 插件与外部微信通道版本不兼容时，停止微信任务并按 Release notes 回退配套版本。

## 5. npm 发布（维护者操作）

### 5.1 发布前

1. 确定产品版本 `X.Y.Z`。
2. 更新 `chub` 和 `@chub/openclaw-plugin` 两个 npm 包的版本，以及 Python 应用和插件协议版本。
3. 运行全量 Python 测试、插件构建、校验和测试；第三方微信适配器补丁单独按实际加载目录复检，不伪装成插件包内容。
4. 运行 `npm pack --dry-run` 检查两个包的内容；Chub 主发行包必须包含 CLI 入口、Python 应用、配置示例和 Quick Worker，插件包必须包含 `dist/`、`openclaw.plugin.json` 和 README。确认 `@chub/openclaw-plugin` 配置为公开发布（`publishConfig.access=public`）。
5. 在干净环境测试 `npm install -g chub@X.Y.Z`、`openclaw plugins install npm:@chub/openclaw-plugin@X.Y.Z --pin`、`chub help` 和 `chub start`。

### 5.2 发布动作

1. 创建并推送 `vX.Y.Z` Git tag。
2. tag CI 只负责构建、测试、版本一致性检查和生成 npm 包预览，不发布 npm。
3. CI 创建 GitHub Draft Release，填写版本说明并检查 npm 安装说明。
4. 维护者确认测试、兼容平台和 Release notes 后发布 Release。
5. 仅由 `release.published` 触发一次受保护的 npm 发布工作流；校验 tag 与两个包版本一致后，发布 `chub@X.Y.Z` 和 `@chub/openclaw-plugin@X.Y.Z`。
6. 发布后在干净 macOS/Ubuntu 设备重新执行安装和启动 smoke test，并检查 `npm view` 返回的版本。

npm Registry 是唯一用户安装渠道；GitHub Release 只负责版本归档、变更说明和 npm 链接，不提供替代下载或安装流程。发布工作流应使用受保护的 CI 凭据，不从个人工作区直接执行正式发布。

## 6. 版本规则

- `MAJOR`：不兼容的 CLI、配置、服务或协议变化。
- `MINOR`：向后兼容的新命令、新功能或可选集成。
- `PATCH`：兼容的修复、文档或安全更新。
- `-rc.N`/`-beta.N`：预发布版本，不进入 `latest`。

`vX.Y.Z` 是 GitHub Release、`chub` 和 `@chub/openclaw-plugin` 的共同版本标识；两个 npm 包必须同版本发布。OpenClaw 和腾讯微信插件版本写入兼容性矩阵，不强制与 Chub 版本号相同。微信调度协议、Quick Worker 协议、Session/任务 schema 等内部版本独立管理；只有对应协议变化时才更新。版本类别、权威来源和升级恢复校验以[总体架构的版本与运行态边界](CHUB_ARCHITECTURE_DESIGN.md#7-版本与运行态边界)为准，本文只负责产品版本、包版本和发布物。

## 7. GitHub Release

GitHub Release 面向维护者和用户提供版本归档，不替代 npm 安装。每个稳定版本使用：

- 标题：`Chub vX.Y.Z`
- Tag：`vX.Y.Z`
- 状态：稳定版；预发布使用 prerelease
- 链接：README 指向 `/releases/latest`

Release notes 至少包含：

1. 本版本变化摘要。
2. Node.js/npm 支持版本和 `npm install -g chub@X.Y.Z`、`chub start`。
3. Chub OpenClaw 插件版本、`openclaw plugins install npm:@chub/openclaw-plugin@X.Y.Z --pin`、兼容的 OpenClaw 版本。
4. 外部微信通道 `@tencent-weixin/openclaw-weixin` 的精确版本、安装、登录、配对命令和兼容性。
5. 支持平台、配置变化、Context Token 复检要求、升级注意事项和已知限制。
6. 回滚版本和失败恢复方式。

Release 不承担安装职责，不上传用于替代 npm 安装的插件压缩包或平台运行包。Release notes 必须链接到 `chub@X.Y.Z`、`@chub/openclaw-plugin@X.Y.Z` 和外部微信通道的官方安装说明；不得上传第三方微信插件、凭据、用户配置或日志。

参考：[GitHub Releases 概览](https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases)、[管理 Releases](https://docs.github.com/en/repositories/releasing-projects-on-github/managing-releases-in-a-repository)、[发布 Node.js 包](https://docs.github.com/en/actions/tutorials/publish-packages/publish-nodejs-packages)。

## 实施准入与待确认决策

本文在以下决策明确前不进入实现；实现开始前，维护者应在 Release/设计记录中固定结果：

| 决策 | 必须明确的结果 |
| --- | --- |
| 包边界与命名 | `chub` 是否承载全部核心运行物；`@chub/openclaw-plugin` 是否独立发布；两个包的公开权限和版本关系。 |
| 首次启动 | `chub start` 如何识别首次运行、创建用户服务、初始化配置并确认 Web/Worker 健康；重复执行的幂等和失败恢复。 |
| 平台与版本矩阵 | Node.js/npm、macOS/Ubuntu、OpenClaw、腾讯微信插件的支持版本和验证责任人。 |
| 微信通道交付 | 外部微信包的官方来源、安装/登录/配对命令、Owner 规则和 Chub Release 的兼容记录方式。 |
| OpenClaw 最小定制维护 | 第三方适配器 Context Token 补丁的首次安装、外部插件升级、补丁不适用和回滚责任边界；详细规则只保留在 OpenClaw 定制文档。 |
| 发布与回滚 | tag、Draft Release、`release.published`、npm 发布权限、双包原子性、失败重试和回滚方式。 |

只有上述结果全部确认，并且目标包的 `bin`、文件内容、公开权限和版本检查可在干净设备复现后，才进入实现和端到端验收。

## 目标交付边界

- 用户安装和升级只使用 npm Registry：Chub 主 CLI 为 `chub`，Chub OpenClaw 插件为 `@chub/openclaw-plugin`。
- `chub` 必须提供可执行的 `chub` bin 和首次启动引导；`@chub/openclaw-plugin` 必须设置 `publishConfig.access=public`，并包含运行产物、插件清单和 README；第三方微信适配器补丁不混入插件包。
- Quick Worker 随 `chub` 一起安装和启动，不提供独立 npm 包或独立用户安装步骤。
- OpenClaw Gateway 与第三方微信通道不属于 Chub 发布物，继续按各自官方文档安装。
- GitHub Release 只归档版本、变更和兼容性信息；不能作为第二个安装渠道。
- 如果两个 npm 包、`chub start` 或 OpenClaw 最小定制复检流程尚未完成验证，发布必须暂停，不得对外提供未验证的安装路径。

## 相关文档与复检

- [README](../README.md)：项目概览、日常服务操作和开发参考。
- [Chub 总体架构](CHUB_ARCHITECTURE_DESIGN.md)：系统进程和状态所有权。
- [Chub Quick Worker 设计](CHUB_QUICK_WORKER_DESIGN.md)：Worker 内部运行、恢复和维护规则。
- [OpenClaw 定制设计](OPENCLAW_CUSTOMIZATION_DESIGN.md)：ClawBot 身份、路由、安全和微信验收。
- [Chub OpenClaw 插件说明](../integrations/openclaw/chub/README.md)：插件协议、维护者构建细节和发布检查；用户安装仍使用 npm 包。
- [集成能力清单](CHUB_INTEGRATION_CAPABILITIES.md)：当前 CLI、插件、API 和微信指令。

触发复检的变化：安装/启动命令、包名、版本来源、Release 资产、ClawBot 协议、Web/Worker 服务关系或用户可见安装步骤发生变化。

## 设计复检与实施准入

- 已核对：当前 CLI、Web/Worker 服务关系、Chub OpenClaw 插件边界、外部 OpenClaw/微信通道安装边界、OpenClaw 最小定制复检规则和现有能力清单。
- 待确认：包边界和命名、首次引导契约、版本矩阵、外部微信安装说明、第三方适配器补丁的维护责任、npm 发布触发和失败恢复。
- 实施后才验收：两个 npm 包的 bin/文件内容、指定版本的 OpenClaw/微信通道组合、Context Token 首次安装与升级复检，以及维护者本人在 macOS/Ubuntu 上完成的 Web 快速交互和真实微信收发结果。
