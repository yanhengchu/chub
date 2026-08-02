const MESSAGE_MARKER = /(?:^|\n)[ \t]*消息内容[：:][ \t]?/;

export function extractVerbatimNotificationMessage(content: string): string | null {
  const match = MESSAGE_MARKER.exec(content);
  if (!match) {
    return null;
  }

  const message = content.slice(match.index + match[0].length);
  if (message.trim().length === 0 || message.length > 4000) {
    return null;
  }
  return message;
}
