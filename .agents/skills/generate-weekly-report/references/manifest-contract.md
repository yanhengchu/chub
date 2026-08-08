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

`report_period` always covers the current period's Monday through Sunday. Source documents may use different reporting windows, such as Monday through Friday, Monday through Saturday, or Tuesday through Saturday. A source belongs to the current period when its declared `usage_period` falls entirely within `report_period`. If the source spans a period boundary, use an explicit `heading-range` or maintainer-confirmed range; never include it solely because its download date or a portion of its content overlaps.

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
  "allowed_markers": []
}
```

`decisions` must be non-empty. `approved_gaps` names every explicitly accepted missing scope. `allowed_markers` contains each complete, exact unresolved phrase that may remain in the formal report; the validator removes only those full phrases before checking for residual markers. Do not whitelist broad words such as “待确认”. Formal report validation fails without this file or when its fingerprint differs.
