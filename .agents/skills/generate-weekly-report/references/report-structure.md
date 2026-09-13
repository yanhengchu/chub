# Formal report structure

Use this default order for the OKR-oriented report, merging or omitting empty optional sections:

1. 业务关键指标
2. 产品体验提升
3. 产品营收提升
4. 项目质量提升
5. AI 工程化推进
6. 白牌迁移推进
7. 其他重点工作
8. 需关注与决策事项
9. 各端周报（必需）

Do not generate a business summary by default. Start with `业务关键指标`, then enter the first OKR directly. Add a summary only after an explicit maintainer request; it must contain 4—6 substantive management conclusions and must not repeat the OKR body.

Write each OKR's original title as the heading and its original content immediately below it with a `目标：` label; do not add a separate OKR overview that repeats the same information. For KPI-oriented OKRs, add `当前进展：` immediately after the target: use the latest comparable source period and state its period. When the source lacks a direct actual-versus-target metric, state that gap instead of treating proxy activity, a version result, or an individual campaign as attainment. Put evidence, work, risks and decisions below the target/current-progress block using concise labels such as `关键进展`、`持续跟进与风险`、`质量与合规` or `待决策`.

When the period has white-label migration work, put its scope, progress, dependencies and risks in the standalone `白牌迁移推进` section, at the same hierarchy as `AI 工程化推进`; do not duplicate them in `项目质量提升` or `其他重点工作`. Omit the section when there is no relevant work. Put other work that is explicitly outside the declared OKRs in `其他重点工作`. Put only cross-cutting risks, resource coordination and management choices in `需关注与决策事项`; do not repeat the detailed body there. Do not invent an “无风险” conclusion to fill the risk section; keep input lists, internal gaps, exclusions, and compilation notes in the verification record.

Preserve critical metric tables where possible: fields, columns, row order, empty cells, owners, status, notes, source period, and units. Use `<br>` for meaningful line breaks inside cells. Do not transpose or delete columns merely for visual compactness.

Keep source periods visible when they differ from the formal report period. Do not compare different windows without stating the difference.
