# Chub OpenClaw Plugin

Integrates OpenClaw with Chub through small, explicitly allowlisted tools that
share one fixed Tailnet client. The current release only provides the optional,
read-only `chub_get_status` tool.

Build and validate:

```bash
npm install
npm run plugin:build
npm run plugin:validate
npm test
```

The OpenClaw plugin config requires a fixed Tailnet base URL. Do not place a
Hub Token in this plugin configuration.
