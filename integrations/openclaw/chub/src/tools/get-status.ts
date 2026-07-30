import { Type } from "typebox";

import { fetchChubStatus, type ChubConfig } from "../client.js";

type ToolContext = { signal?: AbortSignal };

export const getStatusTool = {
  name: "chub_get_status",
  label: "Chub Status",
  description: "检查当前 OpenClaw Gateway 所在设备的 Chub 状态。",
  parameters: Type.Object({}, { additionalProperties: false }),
  optional: true,
  async execute(
    _params: object,
    config: ChubConfig,
    context: ToolContext,
  ) {
    return fetchChubStatus(config, context.signal);
  },
};
