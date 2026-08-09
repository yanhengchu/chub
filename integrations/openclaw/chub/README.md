# Chub OpenClaw Plugin

Integrates OpenClaw with Chub through small, explicitly allowlisted tools that
share one fixed Tailnet client. The plugin currently provides the optional
`chub_get_status` and `chub_send_notification` tools.

Notification requests containing a `消息内容：` marker use verbatim mode. The
plugin captures the pre-model text by run ID and replaces the model-generated
tool argument before execution. Enable the required conversation hook access:

```bash
openclaw config set \
  plugins.entries.chub.hooks.allowConversationAccess true \
  --strict-json
```

Build and validate:

```bash
npm install
npm run plugin:build
npm run plugin:validate
npm test
```

The OpenClaw plugin config requires a fixed Tailnet base URL. Do not place a
Hub Token in this plugin configuration.

## Weixin Chub status test mode

Set `wechatChubStatusMode` to `true` only for the Weixin routing phase. Direct
Weixin messages then call Chub's fixed
`/api/openclaw/wechat-chub-mode/status` endpoint. Chub controls whether the
mode is enabled: `disabled` continues the normal OpenClaw flow; `ready` returns
a handled status confirmation; any unavailable or invalid status returns a
handled failure without Agent or LLM dispatch. Groups and every message while
the flag is disabled keep their normal OpenClaw behavior. This phase does not
submit a Codex task. The current deployment binds exactly one Weixin account,
which is the Owner; do not add a second Owner identifier, sender comparison,
or identity-mapping configuration to this route.
