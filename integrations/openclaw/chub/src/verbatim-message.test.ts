import { describe, expect, it } from "vitest";

import { extractVerbatimNotificationMessage } from "./verbatim-message.js";

describe("extractVerbatimNotificationMessage", () => {
  it("preserves Chinese notification text exactly", () => {
    const content = [
      "调用 chub_send_notification 向 test 飞书群发送普通消息，不要提醒任何人。",
      "消息内容：微信飞书通知链路验收通过。",
    ].join("\n");

    expect(extractVerbatimNotificationMessage(content))
      .toBe("微信飞书通知链路验收通过。");
  });

  it("preserves multiline content after the marker", () => {
    expect(extractVerbatimNotificationMessage(
      "消息内容:\n第一行\n第二行。",
    )).toBe("\n第一行\n第二行。");
  });

  it("does not treat similar prose as a message marker", () => {
    expect(extractVerbatimNotificationMessage(
      "请说明消息内容应该怎么写。",
    )).toBeNull();
  });

  it("rejects an empty message body", () => {
    expect(extractVerbatimNotificationMessage("消息内容：")).toBeNull();
    expect(extractVerbatimNotificationMessage("消息内容： \n\t")).toBeNull();
  });

  it("rejects content exceeding the notification limit", () => {
    expect(extractVerbatimNotificationMessage(
      `消息内容：${"a".repeat(4001)}`,
    )).toBeNull();
  });
});
