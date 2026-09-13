# V business source profile

Check these areas when extracting focus and reviewing coverage:

- Product commercialization: metric changes, memberships, ads, recommendations, version planning, decisions.
- Product/OS: important items, OS versions, cross-team requirements, operations.
- Operations: user, content, paid, and catalog operations.
- Client: read only the `五、VIVO国内` heading range; extract release plans, horizontal issues, core business and technical metrics, and risks. Do not use the common section or other client groups.
- Server: read only the `一、南京服务端 @薛峰` heading range; extract releases, catalog and graph, commercialization, recommendations, campaigns, incidents, and stability. Do not use other server groups.
- Weekly meeting: new problems, explicit conclusions, verification actions, follow-ups.

This profile guides extraction only. Required document roles come from the manifest so sources can evolve independently.

The V weekly-report publisher may set `report_validation.business_metrics_source_role` to `music-product` when a current commercialization metrics source is available. Formal sections remain template-driven: retain a section when this period has relevant content, and omit it when it does not. Keep only the template's chosen structural checks in mapping so a future V template can evolve without changing the generic validator.

For V reports, `music-product` is the current `产品商业化` source when the mapping declares it for metrics. Synchronize `业务关键指标` only from that role's declared usage range in two parts: a single `- **大盘数据表现：...**` item, followed immediately by the source's indicator table. Preserve the description's definition, period and necessary explanation, and state its prior comparable period whenever describing a rise or fall; then preserve the source table's fields, column order, units, source period, empty cells and notes. When the source is not declared or does not provide usable metrics, omit this section rather than filling it from the main report or other client, operations, server or product/OS materials.

When the current V weekly DAU has not been confirmed by Stage A, retain the source placeholder `？W`; do not infer a value. Record the maintainer's fixed confirmation date, normally the Monday after the report period, in the focus checklist and carry the placeholder into the formal report until that value is confirmed.

Place this known DAU placeholder in the focus checklist's `本周需要同步的事项`, not its maintainer-confirmation list, unless a separate business decision is unresolved. In `产品体验提升`'s `当前进展`, keep two bullets: carry forward the previous formal report's available stage and monthly DAU averages, updating them for the latest period available this week, then state the latest complete-week DAU as its actual value when confirmed, otherwise as `？W` with the concrete Monday-after-period supplement date. Do not hard-code H1 or particular months.

When the period includes white-label migration work and the `product` material names controlled migration documents, retain each directly associated document in `【白牌迁移推进】` as `标题：URL`. Do not substitute a general source URL for a named migration document, and do not retain URLs that are not directly paired with a report item.
