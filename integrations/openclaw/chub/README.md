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
