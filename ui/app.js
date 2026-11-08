"use strict";

const $ = (id) => document.getElementById(id);
const state = {
  ws: null,
  running: false,
  currentFile: null,      // 当前打开的文件路径
  currentContent: null,   // 编辑器加载时的原始内容
};

/* ---------------- 初始化 ---------------- */
async function init() {
  bindEvents();
  await loadConfig();
  await loadTree();
  connectWS();
}

async function loadConfig() {
  try {
    const r = await fetch("/api/config");
    const cfg = await r.json();
    $("model-badge").textContent = cfg.has_key ? cfg.model : "未配置 Key";
  } catch (e) { /* ignore */ }
}

async function loadTree() {
  try {
    const r = await fetch("/api/tree");
    const tree = await r.json();
    renderTree(tree);
  } catch (e) {
    $("tree").innerHTML = '<div class="node">加载失败</div>';
  }
}

function renderTree(nodes) {
  const el = $("tree");
  el.innerHTML = "";
  for (const n of nodes) {
    el.appendChild(treeNode(n));
  }
}

function treeNode(n) {
  const div = document.createElement("div");
  div.className = "node " + n.type;
  div.textContent = (n.type === "dir" ? "▸ " : "") + n.name;
  if (n.type === "dir") {
    div.addEventListener("click", () => {
      const kids = div.nextElementSibling;
      if (kids && kids.classList.contains("children")) {
        kids.style.display = kids.style.display === "none" ? "" : "none";
      }
    });
  } else {
    div.addEventListener("click", () => openFile(n.path, div));
  }
  const wrap = document.createElement("div");
  wrap.appendChild(div);
  if (n.children && n.children.length) {
    const kids = document.createElement("div");
    kids.className = "children";
    kids.style.paddingLeft = "14px";
    for (const c of n.children) kids.appendChild(treeNode(c));
    wrap.appendChild(kids);
  }
  return wrap;
}

/* ---------------- 编辑器 ---------------- */
async function openFile(path, nodeEl) {
  const r = await fetch("/api/file?path=" + encodeURIComponent(path));
  if (!r.ok) { alert("打开失败"); return; }
  const data = await r.json();
  state.currentFile = path;
  state.currentContent = data.content;
  $("editor-path").textContent = path;
  $("editor").value = data.content;
  document.querySelectorAll(".node.selected").forEach((e) => e.classList.remove("selected"));
  if (nodeEl) nodeEl.classList.add("selected");
  updateGutter();
}

function updateGutter() {
  const lines = $("editor").value.split("\n").length;
  let g = "";
  for (let i = 1; i <= lines; i++) g += i + "\n";
  $("gutter").textContent = g;
}

function syncScroll() {
  $("gutter").scrollTop = $("editor").scrollTop;
}

async function saveFile() {
  if (!state.currentFile) return;
  const content = $("editor").value;
  const r = await fetch("/api/file", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path: state.currentFile, content }),
  });
  const data = await r.json();
  state.currentContent = content;
  addToolCall({
    name: "write",
    source: "builtin",
    args: { path: state.currentFile },
    text: data.text,
    ok: data.ok,
  });
}

/* ---------------- 对话 / 运行 ---------------- */
function connectWS() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws`);
  state.ws = ws;
  ws.onmessage = (ev) => handleEvent(JSON.parse(ev.data));
  ws.onclose = () => { setRunning(false); setTimeout(connectWS, 2000); };
}

function setRunning(r) {
  state.running = r;
  $("btn-run").disabled = r;
  $("btn-interrupt").disabled = !r;
}

function run() {
  const task = $("task-input").value.trim();
  if (!task || state.running) return;
  $("task-input").value = "";
  addMsg("user", task);
  $("tool-log").innerHTML = "";
  setRunning(true);
  state.ws.send(JSON.stringify({ type: "run", task }));
}

function interrupt() {
  if (!state.running) return;
  state.ws.send(JSON.stringify({ type: "interrupt" }));
  addMsg("system", "已请求中断…");
}

function handleEvent(ev) {
  switch (ev.type) {
    case "step":
      addMsg("system", `— 第 ${ev.step} 步 —`);
      break;
    case "assistant":
      if (ev.text) addMsg("assistant", ev.text);
      break;
    case "tool_call":
      addToolCall({ name: ev.name, source: ev.source, args: ev.args });
      break;
    case "tool_result":
      updateLastToolResult(ev);
      break;
    case "done":
      addMsg("assistant", ev.answer || "(无内容)");
      setRunning(false);
      break;
    case "error":
      addMsg("system", "❌ " + ev.message);
      setRunning(false);
      break;
    case "cancelled":
      addMsg("system", "已中断");
      setRunning(false);
      break;
  }
}

function addMsg(role, text) {
  const m = document.createElement("div");
  m.className = "msg " + role;
  m.textContent = text;
  $("messages").appendChild(m);
  $("messages").scrollTop = $("messages").scrollHeight;
}

function addToolCall(call) {
  const div = document.createElement("div");
  div.className = "call";
  const tag = call.source === "mcp"
    ? '<span class="tag mcp">MCP</span>'
    : '<span class="tag builtin">内置</span>';
  div.innerHTML = `
    <div class="call-head">${tag} ${esc(call.name)}</div>
    <div class="call-args">${esc(JSON.stringify(call.args || {}))}</div>
    <div class="call-result pending">…</div>
  `;
  $("tool-log").appendChild(div);
  $("tool-log").scrollTop = $("tool-log").scrollHeight;
}

function updateLastToolResult(ev) {
  const calls = $("tool-log").querySelectorAll(".call");
  const last = calls[calls.length - 1];
  if (!last) return;
  const resultEl = last.querySelector(".call-result");
  resultEl.classList.remove("pending");
  resultEl.classList.add(ev.ok ? "ok" : "error");
  resultEl.textContent = ev.text;
  if (ev.diff) {
    const diffEl = document.createElement("div");
    diffEl.className = "call-diff";
    diffEl.innerHTML = renderDiff(ev.diff);
    last.appendChild(diffEl);
  }
  $("tool-log").scrollTop = $("tool-log").scrollHeight;
}

function renderDiff(diff) {
  return diff.split("\n").map((line) => {
    if (line.startsWith("+++") || line.startsWith("---")) return `<span class="diff-hunk">${esc(line)}</span>`;
    if (line.startsWith("@@")) return `<span class="diff-hunk">${esc(line)}</span>`;
    if (line.startsWith("+")) return `<span class="diff-add">${esc(line)}</span>`;
    if (line.startsWith("-")) return `<span class="diff-del">${esc(line)}</span>`;
    return esc(line);
  }).join("\n");
}

function esc(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

/* ---------------- 设置 ---------------- */
async function openSettings() {
  const cfg = await (await fetch("/api/config")).json();
  $("cfg-base-url").value = cfg.base_url;
  $("cfg-model").value = cfg.model;
  $("cfg-api-key").value = "";
  $("settings-dialog").showModal();
}

async function saveSettings() {
  const body = {
    base_url: $("cfg-base-url").value.trim(),
    model: $("cfg-model").value.trim(),
    persist: $("cfg-persist").checked,
  };
  const key = $("cfg-api-key").value.trim();
  if (key) body.api_key = key;
  await fetch("/api/config", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  $("settings-dialog").close();
  await loadConfig();
}

/* ---------------- 事件绑定 ---------------- */
function bindEvents() {
  $("btn-run").addEventListener("click", run);
  $("btn-interrupt").addEventListener("click", interrupt);
  $("btn-refresh").addEventListener("click", loadTree);
  $("btn-save").addEventListener("click", saveFile);
  $("btn-clear-log").addEventListener("click", () => ($("tool-log").innerHTML = ""));
  $("btn-settings").addEventListener("click", openSettings);
  $("btn-settings-save").addEventListener("click", saveSettings);
  $("btn-settings-cancel").addEventListener("click", () => $("settings-dialog").close());
  $("editor").addEventListener("input", updateGutter);
  $("editor").addEventListener("scroll", syncScroll);
  $("task-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) run();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "s" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); saveFile(); }
  });
}

init();
