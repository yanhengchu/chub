# Hub 第二阶段高层计划

> 当前状态：阶段方向已确定，具体能力清单待探索。

## 1. M1：使用场景探索

- [ ] 收集 macOS 与 Ubuntu 的日常查看、检查和维护场景。
- [ ] 建立候选能力清单，记录价值、频率、风险和适用平台。
- [ ] 区分只读查询、诊断检查和状态变更操作。
- [ ] 选出首批小范围能力并补充具体验收标准。
- [x] 比较 PTY、remote-control 和 app-server，保留 `exec/resume` 作为固定任务补充方案。
- [x] 明确 PTY Session、节点页入口、终端子页面和 Codex 原生权限交互边界。
- [x] 完成 PTY/Web Terminal 手机端首轮可用性实验。
- [x] 统一发现本机全部未归档 Codex Session，支持新建、进入、恢复、停止、归档
  和永久删除。
- [x] 提供用户目录、Workspace 和 Chub 三个固定工作目录入口。
- [x] 验证 SessionStart hook 可取得 Codex session ID，并按 ID 恢复会话。
- [x] 实现 Codex Turn Hook 状态记录和“执行中/等待输入”页面展示。
- [x] 在真实 Codex Session 中验收 Turn Hook 信任及“执行中/等待输入”状态切换。
- [x] 验证 FastAPI 可代理 ttyd HTTP 和双向 WebSocket。
- [x] 确认直接 ttyd 断线会结束 Codex 子进程，正式方案增加 tmux 保持层。
- [ ] 完成 tmux 断线恢复、Codex resume 和进程清理的双平台验证。
- [x] 节点页移除“系统检查”和“版本信息”，暂时保留“Codex 检查”，增加
  Codex PTY Session 面板。
- [x] 实现独立终端子页面，以及返回后恢复 Codex PTY 面板。
- [x] 确定 ttyd 只监听本机，由 Chub 统一代理 HTTP/WebSocket。
- [x] 实现绑定 Session 的短期 HttpOnly 凭证，保护终端 HTTP/WebSocket。
- [x] 保留并整理 remote-control 与 app-server 候选方案。

产出：经过评审的首批能力清单，而不是完整阶段功能列表。

## 2. M2：可信 Tailscale 网络边界

- [x] 明确个人三设备 tailnet 为第二阶段可信网络边界。
- [x] 支持识别 Tailscale IPv4 与 IPv6 地址范围。
- [x] `chub install/start/restart` 在非 Tailscale 监听地址时给出警告。
- [x] 应用记录 Codex PTY 是否满足网络启用条件，并在 API 和页面展示不可用原因。
- [ ] 将 macOS 与 Ubuntu 正式配置固定为各自 Tailscale IP。
- [ ] 验证普通局域网地址无法访问，tailnet 内可信设备可正常访问。
- [x] Codex PTY 接入服务端网络门禁，非 Tailscale 监听地址拒绝启动终端。

产出：基础服务可继续运行，但 Codex PTY 只能在 Tailscale 监听地址下由服务端
启用；PTY 内部权限仍由 Codex CLI 原生管理。

## 3. M3：首批能力交付

- [ ] 按现有任务结构实现首批节点能力。
- [ ] 补充输入校验、超时、错误语义和安全边界。
- [ ] 调整管理页面的名称、说明和结果展示。
- [ ] 完成自动化测试和目标平台实机验收。
- [ ] 记录实际使用反馈。

产出：一批可以在日常使用中持续验证的节点能力。

## 4. M4：能力迭代

- [ ] 根据反馈保留、优化或删除首批能力。
- [ ] 从候选清单选择下一批高价值能力。
- [ ] 评估任务数量增长后是否需要分类、搜索或更紧凑的展示。
- [ ] 评估是否存在引入受控状态变更操作的充分需求。
- [ ] 持续完成自动化测试和双平台回归验证。

产出：经过多轮实际使用筛选的能力集合。

## 5. M5：阶段总结

- [ ] 汇总高频能力、低价值能力和未解决场景。
- [ ] 评估现有单节点产品形态的承载能力。
- [ ] 判断是否进入多节点统一入口、正式发布交付或继续能力扩展。
- [ ] 更新第二阶段验收记录并形成下一阶段建议。

## 6. 当前任务

Codex PTY 已完成 macOS 首轮实现和手机实测，下一步完成 Ubuntu 同等回归以及
M2 网络可达性验收；其他节点能力继续按 M1 场景探索结果确定。
