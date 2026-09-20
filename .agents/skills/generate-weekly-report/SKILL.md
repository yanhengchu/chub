---
name: generate-weekly-report
description: Generate a traceable local Chinese business weekly report from Markdown files exported by Chub automation. Use when Codex needs to adapt the downloaded main and linked reports into a manifest, validate input roles and heading ranges, produce a maintainer-confirmed focus checklist, generate the final local Markdown report, or review that report against its downloaded sources.
---

# Generate Weekly Report

Generate local Markdown reports in two gated stages. Treat every automation download as read-only and write all generated artifacts under the reporting-period workspace.

## Prepare inputs

1. Derive the current period: it is the Monday-to-Sunday week whose processing window starts on Wednesday and ends on the following Tuesday, the fixed reporting day. Locate that period's single current `inputs/` and the current source documents. A source may use a single reporting date or a shifted range such as Monday-to-Friday or a range starting before Monday; its declared reporting date or range end must fall within the current period.
2. If `manifest.json` does not exist, create a small explicit mapping JSON and run:

   ```bash
   python3 scripts/adapt_downloads.py \
     --data-root <project-data-directory> \
     --source-root <data/artifacts/weekly-reports/period/inputs> \
     --workspace <data/artifacts/weekly-reports/period> \
     --mapping <mapping.json>
   ```

3. Map roles by known document identity, never by linked-file order alone. Require every role listed in `required_roles`.
4. Run `python3 scripts/validate_weekly_report.py inputs --manifest <manifest.json>`.
5. Stop on an invalid Monday-to-Sunday report period, a source without an explicit `usage_period`, a range whose end falls outside the current period, missing required sources, unsafe paths, unreadable content, unresolved heading boundaries, changed hashes, or a blocking `content_status`. Record any maintainer-approved gap in the confirmation for traceability, but do not treat it as a validator bypass; continue only after the actual usable inputs are `ready` or explicitly `manually-approved`.

The adapter does not copy, move, or modify downloads. It records a constrained `source_root` inside the same period workspace and paths relative to it. Read [references/manifest-contract.md](references/manifest-contract.md) when creating or diagnosing a mapping.

## Stage A: confirm focus

Read the `重点关注内容` section of the downloaded `V 国内业务周报` before reading the declared `usage` ranges of the other sources. Treat every item in that section as an owner-provided priority signal: reconcile it against current source facts and classify it as included, merged, downgraded, excluded, or awaiting confirmation. Do not silently omit it, and do not turn an unverified concern into a conclusion.

Then read only each document's declared `usage` range. Read [references/v-report-profile.md](references/v-report-profile.md) while extracting V-business coverage and [references/review-rules.md](references/review-rules.md) for fact handling.

Use [assets/focus-checklist-template.md](assets/focus-checklist-template.md) as the editable structure baseline. Create `output/本期工作重点确认清单-<周期>.md` with exactly two business-content sections, both using short lists:

1. `本周需要同步的事项`: confirmed facts that merit weekly communication, including key numbers, dates, versions, states, ownership boundaries, and every main-report priority item classified as included or merged. End each item with its source role label such as `【来源：产品/OS】`; use multiple labels when needed. When the main report declares OKRs, identify the applicable OKR title inline; do not duplicate target text or create separate OKR sections.
2. `需要维护者确认的重点事项`: every main-report priority item classified as downgraded, excluded, or awaiting confirmation, plus material conflicts, missing information, interpretation choices, and formal-report inclusion or weakening decisions. State exactly what requires confirmation, the possible conclusion or decision, and the resulting formal-report impact; do not turn an unverified concern into a conclusion.

Every main-report priority item must appear in one of the two lists. A known V-weekly DAU placeholder `？W` with a scheduled supplement on the Monday after the report period is a synchronization fact: put it in `本周需要同步的事项`, state that concrete date, and do not list it as a maintainer confirmation unless an actual business decision is unresolved. Do not add standalone sections for OKRs, other work, source coverage, priority reconciliation, proposed narrative, or conflicts; fold the necessary context into the relevant list item. Keep a short input-period/Manifest-fingerprint header and a `维护者确认结果` section after the two business sections. The confirmation section is a gate record, not a third business-content section.

Keep entries factual and short. Use concrete dates for resolved or scheduled events. Do not draft polished report prose yet.

Pause for explicit maintainer confirmation. The final session reply must use this three-part shape: first `已完成 Stage A，生成本期工作重点确认清单：`; then a `需要确认的事项清单` heading and the short items from `需要维护者确认的重点事项` (when there are no such items, state `- 无待确认事项。`); finally `当前等待维护者确认后再进入 Stage B。` Do not repeat the full checklist or draft formal-report prose. Persist the confirmation time, decisions, approved gaps, final interpretations, and the manifest fingerprint in the checklist. Revalidate inputs immediately before Stage B; regenerate the checklist if any source hash changed.

After confirmation, also write `output/weekly-report-confirmation.json` as the deterministic gate described in [references/manifest-contract.md](references/manifest-contract.md). Record the output-relative checklist path and its SHA-256, and keep its decisions and Manifest fingerprint consistent with the checklist; the JSON supplements rather than replaces the human-readable confirmation.

## Stage B: generate and review

Read [references/report-structure.md](references/report-structure.md) and use [assets/formal-weekly-report-template.md](assets/formal-weekly-report-template.md) as the editable structure baseline. When `Manifest.report_validation.business_metrics_source_role` names a usable current source, synchronize `业务关键指标` only from that material's declared usage range: write its market-wide indicator description as one `- **大盘数据表现：...**` item directly below the section heading, then place its indicator table immediately after that item. Preserve the description's actual definition, period and necessary explanation, and state the comparison baseline whenever describing a rise or fall; preserve the table's fields, column order, units, source period, empty cells and notes. If no usable metrics source is declared, omit this section and note the absence in the verification record; do not substitute another source or stop Stage B. In `产品体验提升`'s `当前进展`, write two bullets from the usable current sources: state the current confirmed stage and available DAU averages, then state the current complete-week DAU as its actual value when confirmed, otherwise as `？W` with its concrete Monday-after-period supplement date. Omit unavailable facts rather than carrying them from an earlier report. Do not hard-code H1 or particular months. When this period has white-label migration work, create `【白牌迁移推进】` as a standalone section at the same hierarchy as `【AI 工程化推进】`; organize its current progress by migration scope, current stage, completed work and next milestone, then put dependencies, risks and coordination in its risk subsection. Preserve any directly named, controlled migration-material links as `标题：URL` in that section. Omit it when there is no relevant work. Do not use other sources to infer missing values. Use the confirmed checklist as the narrative, then return to the declared source ranges to verify every fact. The current Manifest's `report_validation` remains authoritative when it differs from the template.

Write `output/本期业务周报-<周期>.md`. Use the confirmed report structure and a final `各端周报` section where every Manifest source entry is exactly `<来源标题>：<source_url>` on its own list item. Apply any profile-specific `report_validation` requirements from the mapping. Do not generate a business summary by default; start directly with the business metrics or the first confirmed body section. Add a business summary only when the maintainer explicitly asks for one, and never repeat its content in the body. Do not put an input inventory or internal compilation notes at the top.

Perform three reviews:

1. Completeness: account for every current source focus. Note material exclusions or unavailable template content in the verification record when they affect interpretation.
2. Accuracy: verify numbers, dates, versions, names, links, status, period, arithmetic direction, and responsibility.
3. Copy: normalize headings, terminology, units, tense, duplication, and clarity without deleting needed context.

After every maintainer correction, search the full report for stale versions of the affected fact and update the summary (when present), body, risks, and links together.

Write `output/周报生成核对记录-<周期>.md` containing the actual inputs and ranges, approved gaps, conflicts and resolutions, exclusions, source coverage table, maintainer corrections, residual searches, arithmetic checks, and validation output.

Finally run:

```bash
python3 scripts/validate_weekly_report.py report \
  --manifest <manifest.json> \
  --confirmation <weekly-report-confirmation.json> \
  --report <formal-report.md>
```

Do not call the report complete while deterministic validation or a review item is unresolved. Stop after the local Markdown report and verification record are complete; never modify source files or automation state.
