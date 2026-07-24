---
name: generate-weekly-report
description: Generate a traceable local Chinese business weekly report from Markdown files exported by Chub automation. Use when Codex needs to adapt the downloaded main and linked reports into a manifest, validate input roles and heading ranges, produce a maintainer-confirmed focus checklist, generate the final local Markdown report, or review that report against its downloaded sources.
---

# Generate Weekly Report

Generate local Markdown reports in two gated stages. Treat every automation download as read-only and write all generated artifacts under the reporting-period workspace.

## Prepare inputs

1. Locate the reporting period, the previous formal report, and the current source documents.
2. If `manifest.json` does not exist, create a small explicit mapping JSON and run:

   ```bash
   python3 scripts/adapt_downloads.py \
     --data-root <project-data-directory> \
     --source-root <automation-download-root> \
     --workspace <data/weekly-reports/period> \
     --mapping <mapping.json>
   ```

3. Map roles by known document identity, never by linked-file order alone. Require `previous-report` plus every role listed in `required_roles`.
4. Run `python3 scripts/validate_weekly_report.py inputs --manifest <manifest.json>`.
5. Stop on missing required sources, unsafe paths, unreadable content, unresolved heading boundaries, changed hashes, or a blocking `content_status`. Continue with a gap only after the maintainer explicitly approves its exact scope.

The adapter does not copy, move, or modify downloads. It records a constrained `source_root` and paths relative to it. Read [references/manifest-contract.md](references/manifest-contract.md) when creating or diagnosing a mapping.

## Stage A: confirm focus

Read only each document's declared `usage` range. Read [references/v-report-profile.md](references/v-report-profile.md) while extracting V-business coverage and [references/review-rules.md](references/review-rules.md) for fact handling.

Create `output/本周工作重点确认清单-<周期>.md` with:

- proposed inclusions, merges/weakening, and exclusions;
- continuations from the previous report;
- conflicts, missing information, and questions;
- a proposed narrative of the formal report;
- source-by-source coverage;
- a `维护者确认结果` section.

Keep entries factual and short. Preserve key numbers, dates, versions, states, and ownership boundaries. Do not draft polished report prose yet.

Pause for explicit maintainer confirmation. Persist the confirmation time, decisions, approved gaps, final interpretations, and the manifest fingerprint in the checklist. Revalidate inputs immediately before Stage B; regenerate the checklist if any source hash changed.

After confirmation, also write `output/weekly-report-confirmation.json` as the deterministic gate described in [references/manifest-contract.md](references/manifest-contract.md). Keep its decisions consistent with the checklist; the JSON supplements rather than replaces the human-readable confirmation.

## Stage B: generate and review

Read [references/report-structure.md](references/report-structure.md). Use the confirmed checklist as the narrative, then return to the declared source ranges to verify every fact.

Write `output/本周业务周报-<周期>.md`. Include 4–6 substantive summary items and a final `各端周报` section with source links. Do not put an input inventory or internal compilation notes at the top.

Perform three reviews:

1. Completeness: account for every source focus and every previous-week continuation.
2. Accuracy: verify numbers, dates, versions, names, links, status, period, arithmetic direction, and responsibility.
3. Copy: normalize headings, terminology, units, tense, duplication, and clarity without deleting needed context.

After every maintainer correction, search the full report for stale versions of the affected fact and update the summary, body, risks, and links together.

Write `output/周报生成核对记录-<周期>.md` containing the actual inputs and ranges, approved gaps, conflicts and resolutions, exclusions, source coverage table, maintainer corrections, residual searches, arithmetic checks, and validation output.

Finally run:

```bash
python3 scripts/validate_weekly_report.py report \
  --manifest <manifest.json> \
  --confirmation <weekly-report-confirmation.json> \
  --report <formal-report.md>
```

Do not call the report complete while deterministic validation or a review item is unresolved. Stop after the local Markdown report and verification record are complete; never modify source files or automation state.
