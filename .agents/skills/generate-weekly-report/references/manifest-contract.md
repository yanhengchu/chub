# Manifest contract

The adapter mapping is JSON:

```json
{
  "version": 1,
  "report_period": {
    "start": "2026-07-20",
    "end": "2026-07-26",
    "timezone": "Asia/Shanghai"
  },
  "report_validation": {
    "required_sections": ["业务关键指标", "各端周报"],
    "required_section_text": {
      "产品体验提升": ["目标：", "当前进展："]
    },
    "checklist_required_sections": ["本周需要同步的事项", "需要维护者确认的重点事项"]
  },
  "required_roles": [
    "previous-report",
    "music-product",
    "product",
    "operations",
    "client",
    "server"
  ],
  "documents": [
    {
      "role": "previous-report",
      "path": "linked/上周业务周报.md",
      "title": "上周业务周报",
      "source_url": "https://tenant.feishu.cn/wiki/document-id",
      "download_status": "succeeded",
      "content_status": "ready",
      "usage": {"mode": "reference-only"}
    },
    {
      "role": "product",
      "path": "linked/产品周报.md",
      "title": "产品周报",
      "download_status": "succeeded",
      "content_status": "ready",
      "usage_period": {"start": "2026-07-20", "end": "2026-07-23"},
      "usage": {
        "mode": "heading-range",
        "start_heading": "日期：2026-7-23",
        "end_heading": "日期：2026-7-16"
      }
    }
  ]
}
```

`data_root` and `source_root` are supplied on the command line and stored relative to the generated manifest. `source_root` must remain inside `data_root`. Document paths are relative to `source_root`, cannot contain `..`, and must resolve inside that root.

Supported roles:

- Required by the V profile: `previous-report`, `music-product`, `product`, `operations`, `client`, `server`.
- Optional: `weekly-meeting`, `manual-confirmation`, `supplemental-material`.

The mapping declares the actual required roles so future profiles can change without changing scripts.

Supported `content_status`: `ready`, `needs-review`, `incomplete`, `still-editing`, `stale`, `manually-approved`. Only `ready` and `manually-approved` pass input validation.

Always set `download_status` and `content_status` explicitly. The adapter never infers download success or content readiness from file existence.

`report_period` always covers one complete Monday-through-Sunday week. Each non-reference source must declare `usage_period.start` and `usage_period.end` as ISO dates. A source belongs to the current period when its declared reporting date or range end falls within the report period; its start may precede Monday. The adapter and input validator enforce this rule. `reference-only` material, such as the previous formal report, is exempt because it is background rather than current-period fact input. A cross-period source still needs an explicit `heading-range` or maintainer-confirmed range; never include it solely because its download date or a portion of its content overlaps.

`report_validation` is optional and keeps profile-specific formal-report checks out of the generic skill. `required_sections` lists headings that must occur in the formal report. `required_section_text` maps a section heading to exact labels or text that must occur within that section. `checklist_required_sections` lists the two business-content headings required in the confirmed focus checklist; `维护者确认结果` is always required by the validator. Other profiles can omit or replace this block.

Supported usage modes:

- `reference-only`
- `whole-document`
- `heading-range`

For `heading-range`, provide `start_heading` and either `end_heading` or `to_end_of_document: true`. Boundaries must be unique and ordered. Heading comparison removes Markdown heading/escape/emphasis syntax, collapses spaces, and normalizes recognizable dates; it does not alter source content.

The generated manifest adds file size, modification time, SHA-256, boundary match details, and a manifest fingerprint. Re-run the adapter when the mapping intentionally changes. Re-run input validation before formal generation to detect source changes.

After the maintainer confirms Stage A, create `output/weekly-report-confirmation.json`:

```json
{
  "status": "confirmed",
  "confirmed_at": "2026-07-24T20:00:00+08:00",
  "manifest_fingerprint": "<manifest fingerprint>",
  "decisions": ["纳入事项 A", "弱化事项 B"],
  "approved_gaps": [],
  "allowed_markers": [],
  "checklist": {
    "path": "本期工作重点确认清单-2026-07-20至2026-07-26.md",
    "sha256": "<checklist sha256>"
  }
}
```

`decisions` must be non-empty. `approved_gaps` records every explicitly accepted missing scope for traceability; the current validator does not use it to bypass missing, failed, or otherwise blocking input validation. Only usable inputs with `ready` or explicitly `manually-approved` status can pass the input gate. `allowed_markers` contains each complete, exact unresolved phrase that may remain in the formal report; the validator removes only those full phrases before checking for residual markers. Do not whitelist broad words such as “待确认”. `checklist` binds the human-readable confirmation to the JSON gate with a safe output-relative path and SHA-256. The checklist must retain the current Manifest fingerprint and its configured required headings. Formal report validation fails without this binding or when either input changes.
