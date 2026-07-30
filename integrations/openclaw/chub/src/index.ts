import { Type } from "typebox";
import { defineToolPlugin } from "openclaw/plugin-sdk/tool-plugin";

import { getStatusTool } from "./tools/get-status.js";

const configSchema = Type.Object({
  baseUrl: Type.Optional(Type.String({
    description: "Fixed Chub base URL on the local Tailnet.",
  })),
  timeoutMs: Type.Optional(Type.Integer({
    minimum: 500,
    maximum: 10_000,
    default: 3_000,
  })),
});

export default defineToolPlugin({
  id: "chub",
  name: "Chub",
  description: "Use explicitly allowlisted Chub capabilities through a fixed Tailnet connection.",
  configSchema,
  tools: (tool) => [tool(getStatusTool)],
});
