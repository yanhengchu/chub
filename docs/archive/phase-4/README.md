# 第四阶段归档

> 归档状态：第四阶段已于 2026-08-15 正式闭环，归档内容冻结，仅用于历史追溯。

## 阶段范围

第四阶段完成 Chub 可维护性整理：建立首页额度真实浏览器回归，归一前端 AI 额度数据所有权，整理微信调度测试与纯逻辑边界，拆分快速交互 Session/时间线视图，并完成全局所有权复核。

本阶段保持页面入口、公开 API、认证、状态文件、插件协议、微信路由和 Quick Worker 运行语义不变。

## 归档内容

| 文档 | 作用 |
| --- | --- |
| [任务与验收记录](TASKS.md) | M1–M6 的目标、实施边界、验证结果和最终完成状态 |

最终验证基线为 Python 全量测试 `900 passed, 36 skipped`、OpenClaw 插件测试 `34 passed`、Ubuntu 真实 Chrome 浏览器回归 `36 passed`。浏览器项默认不进入普通全量测试，需要显式启用。

## 当前替代

- 当前前端分层、快速交互页面边界和 UI 规则见 [Chub 前端 UI 模块化设计](../../FRONTEND_UI_DESIGN.md)。
- 当前额度来源、缓存和响应式展示规则见 [Chub AI 额度与用量采集设计](../../AI_QUOTA_USAGE_DESIGN.md)。
- 当前微信 Chub 调度、身份、状态和通知边界见 [Chub–OpenClaw 接入设计](../../CHUB_OPENCLAW_INTEGRATION_DESIGN.md)。
- 当前产品入口、测试方式和文档管理规则见[项目 README](../../../README.md)。
