const $ = (id) => document.getElementById(id);
let streaming = false;
let viewing = null;
let configured = true;
let openReply = null;

function append(role, text) {
  const div = document.createElement("div");
  div.className = `msg ${role}`;
  div.textContent = text;
  $("transcript").appendChild(div);
  $("transcript").scrollTop = $("transcript").scrollHeight;
  return div;
}

function note(text) {
  const div = document.createElement("div");
  div.className = "note";
  div.textContent = text;
  $("transcript").appendChild(div);
  $("transcript").scrollTop = $("transcript").scrollHeight;
}

function setEntry(html) { $("diary").innerHTML = html; }

async function loadToday() {
  const data = await (await fetch("/api/today")).json();
  viewing = null;
  configured = data.configured;
  $("back").hidden = true;
  $("date").textContent = data.date;
  $("banner").hidden = configured;
  if (!configured) {
    $("banner").textContent = "还没有配置 API key：请填写 ~/DayKeep/config.toml 里的 api_key，然后重启。";
  }
  $("transcript").innerHTML = "";
  for (const m of data.transcript) append(m.role, m.text);
  setEntry(data.entry_html);
}

async function loadHistoryList() {
  const days = await (await fetch("/api/days")).json();
  const sel = $("history");
  sel.innerHTML = '<option value="">往日…</option>';
  for (const d of days) sel.add(new Option(d, d));
}

async function showDay(day) {
  if (!day) { await loadToday(); await loadHistoryList(); return; }
  const res = await fetch(`/api/days/${day}`);
  if (!res.ok) return;
  const data = await res.json();
  viewing = day;
  $("date").textContent = day;
  $("back").hidden = false;
  setEntry(data.entry_html);
}

function applyEvent(name, data) {
  if (name === "day") {
    $("date").textContent = data.date;
  } else if (name === "delta") {
    if (!openReply) openReply = append("assistant", "");
    openReply.textContent += data.text;
    $("transcript").scrollTop = $("transcript").scrollHeight;
  } else if (name === "tool") {
    note("✎ " + data.label);
  } else if (name === "entry") {
    if (!viewing) setEntry(data.html);
  } else if (name === "trimmed") {
    note(`更早的 ${data.dropped_turns} 轮对话已不在 AI 的直接记忆中，需要时它会自己查找`);
  } else if (name === "done") {
    openReply = null;
  } else if (name === "error") {
    note("⚠ " + data.message);
    openReply = null;
  }
}

async function send() {
  const text = $("input").value.trim();
  if (!text || streaming || !configured) return;
  streaming = true;
  $("send").disabled = true;
  $("input").value = "";
  append("user", text);
  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ text }),
    });
    if (res.status === 413) {
      note("⚠ 这条太长了，分几次发");
      return;
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const blocks = buffer.split("\n\n");
      buffer = blocks.pop();
      for (const block of blocks) {
        const lines = block.split("\n");
        const name = (lines.find((l) => l.startsWith("event: ")) || "").slice(7);
        const raw = (lines.find((l) => l.startsWith("data: ")) || "").slice(6);
        if (!name) continue;
        applyEvent(name, raw ? JSON.parse(raw) : {});
      }
    }
  } catch (err) {
    note("⚠ 网络错误，请重试");
  } finally {
    streaming = false;
    openReply = null;
    $("send").disabled = false;
  }
}

$("send").onclick = send;
$("input").onkeydown = (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    send();
  }
};
$("history").onchange = (e) => showDay(e.target.value);
$("back").onclick = () => loadToday();
loadToday();
loadHistoryList();
