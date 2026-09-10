# Chub 正式部署包：AI 安装指引

先解压部署包，并在解压后的项目根目录执行操作。仅支持 macOS 或 Ubuntu；不得把来源设备的本机配置、账号、Token、浏览器数据或运行态复制到目标设备。

1. 确认 Python 3.12+ 可用，创建 `.venv` 并执行 `.venv/bin/python -m pip install -r requirements.txt`。
2. 从 `config/settings.example.yaml` 创建 `config/settings.local.yaml`。请维护者填写节点、可信网络及需要的本机配置；不要猜测或写入凭据。
3. 执行 `./scripts/chub install`，再用 `./scripts/chub check` 确认 Chub Web 与 Quick Worker 最终健康状态。
4. 如需要 AI Runtime 或微信任务编排，在 Chub 设置页分别导入 `bundled-modules/` 中的 ZIP；导入与启用均由维护者明确选择。
5. OpenClaw 是可选项。维护者选择接入时，按 `integrations/openclaw/chub/README.md` 和 `docs/OPENCLAW_CUSTOMIZATION_DESIGN.md` 的已验证基线安装、构建插件并检查 Gateway。未安装 OpenClaw 不影响 Chub 核心部署。

涉及 Codex 登录、OpenClaw/微信账号、Owner 绑定、凭据或真实微信收发时停止并请维护者完成。后台健康记录不能替代维护者在微信客户端的实际验收。
