# Hub 第一阶段验收记录

## 1. 验收结论

第一阶段功能、自动化测试和 Ubuntu 实机检查通过，代码已具备 macOS 与 Ubuntu
双节点独立运行能力。最终关闭 M6 前，仍需在 MacBook 记录自动化测试与 Codex
检查结果，并完成人工断机隔离检查。

当前结论：**有条件通过，等待 MacBook 与双机隔离证据后转为正式通过。**

## 2. 自动化与静态验证

Ubuntu 节点于 2026-07-19 使用当前 `main` 工作树验证：

- `python -m pytest`：82 项通过。
- `python -m pip check`：依赖完整，无冲突。
- Python 源码编译：通过。
- `app/web/static/app.js` JavaScript 语法检查：通过。
- Git 空白与补丁格式检查：通过。

自动化测试覆盖配置、平台检测、健康检查、认证、状态、任务注册与执行、命令
超时、日志读取、API 错误结构、页面资源和安全响应头。

## 3. Ubuntu 实机结果

- 节点身份：`Ubuntu`。
- 实际平台：`ubuntu`。
- 主机名：`chuyh-box`。
- 状态采集：CPU、内存和磁盘指标正常返回。
- Docker 客户端：29.6.1。
- Docker Compose：5.3.0。
- Docker Server：29.6.1。
- Docker 容器概况：正常返回。
- Ubuntu 自动化测试：82 项通过。

## 4. MacBook 待记录项

在 MacBook 的同一版本代码目录执行：

```bash
.venv/bin/python -m pytest
.venv/bin/python main.py
```

然后在管理页面执行“Codex 检查”，记录：

- 自动化测试通过数量。
- macOS 平台识别结果。
- Codex 路径和版本。
- 手机访问状态、任务和日志结果。

## 5. 双机隔离待记录项

1. 保持 MacBook 与 Ubuntu 两个节点在线，手机分别访问两个地址。
2. 停止 MacBook Hub，确认 Ubuntu 页面、状态、任务和日志仍可使用。
3. 恢复 MacBook，停止 Ubuntu Hub，确认 MacBook 对应能力仍可使用。
4. 恢复两个节点并确认各自使用独立 Token 和 `settings.local.yaml`。

## 6. 遗留与后续优化

- 第一阶段仍以可信局域网或虚拟组网为部署边界，不直接暴露公网。
- 浏览器端目前以源码约束和人工手机验收为主；后续可增加 Playwright 端到端测试。
- 依赖使用兼容版本范围；后续正式发布可增加跨平台锁定文件。
- 应用版本同时存在于包元数据和节点配置；下一阶段可收敛为单一版本来源。
- 系统服务、自启动、升级和回滚流程留待下一阶段设计。
