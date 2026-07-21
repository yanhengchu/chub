"use strict";

const SESSION_TOKEN_KEY = "hub.sessionToken";
const LOCAL_TOKEN_KEY = "hub.savedToken";
const source = document.querySelector("#detail-log-source");
const output = document.querySelector("#detail-logs-output");
const message = document.querySelector("#detail-logs-message");
const refresh = document.querySelector("#refresh-detail-logs");
const earlier = document.querySelector("#load-earlier-logs");
const download = document.querySelector("#download-detail-logs");
let nextCursor = null;

function token() {
  return sessionStorage.getItem(SESSION_TOKEN_KEY) || localStorage.getItem(LOCAL_TOKEN_KEY) || "";
}

function setMessage(value, error = false) {
  message.textContent = value;
  message.className = error ? "message message-error" : "message";
}

async function request(path) {
  const response = await fetch(path, {
    cache: "no-store",
    headers: { Authorization: `Bearer ${token()}` },
  });
  if (!response.ok) {
    throw new Error(response.status === 401 ? "请返回首页连接节点后再查看日志。" : "日志读取失败。");
  }
  return response;
}

async function loadLogs(append = false) {
  const cursor = append && nextCursor ? `&before=${nextCursor}` : "";
  try {
    const response = await request(`/api/logs/page?source=${source.value}&lines=500${cursor}`);
    const payload = await response.json();
    const lines = payload.data.lines;
    output.textContent = append && output.textContent
      ? `${lines.join("\n")}\n${output.textContent}`
      : (lines.join("\n") || "当前日志为空。");
    nextCursor = payload.data.next_cursor;
    earlier.disabled = nextCursor === null;
    setMessage(`已读取 ${lines.length} 行。`);
  } catch (error) {
    setMessage(error.message || "日志读取失败。", true);
  }
}

async function downloadLog() {
  try {
    const response = await request(`/api/logs/download?source=${source.value}`);
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `${source.value}.log`;
    link.click();
    URL.revokeObjectURL(url);
  } catch (error) {
    setMessage(error.message || "日志下载失败。", true);
  }
}

refresh.addEventListener("click", () => loadLogs());
earlier.addEventListener("click", () => loadLogs(true));
download.addEventListener("click", downloadLog);
source.addEventListener("change", () => loadLogs());
loadLogs();
