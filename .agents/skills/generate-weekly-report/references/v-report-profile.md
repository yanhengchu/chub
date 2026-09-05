# V business source profile

Check these areas when extracting focus and reviewing coverage:

- Product commercialization: metric changes, memberships, ads, recommendations, version planning, decisions.
- Product/OS: important items, OS versions, cross-team requirements, operations.
- Operations: user, content, paid, and catalog operations.
- Client: read only the `五、VIVO国内` heading range; extract release plans, horizontal issues, core business and technical metrics, and risks. Do not use the common section or other client groups.
- Server: read only the `一、南京服务端 @薛峰` heading range; extract releases, catalog and graph, commercialization, recommendations, campaigns, incidents, and stability. Do not use other server groups.
- Weekly meeting: new problems, explicit conclusions, verification actions, follow-ups.

This profile guides extraction only. Required document roles come from the manifest so sources can evolve independently.

The V weekly-report publisher must write this profile contract into `mapping.json` and `manifest.json`: require `业务关键指标`, the four declared OKR headings, and `各端周报`; require `目标：` for every OKR; require `当前进展：` for the first three KPI-oriented OKRs; and require the focus checklist's `本周需要同步的事项` and `需要维护者确认的重点事项` headings. Keep this mapping-owned so a future V template can evolve without changing the generic validator.

When the current V weekly DAU has not been confirmed by Stage A, retain the source placeholder `？W`; do not infer a value. Record the maintainer's fixed confirmation date, normally the Monday after the report period, in the focus checklist and carry the placeholder into the formal report until that value is confirmed.
