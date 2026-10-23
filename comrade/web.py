"""A local test page: paste text, see ranked passages and the LLM's quote picks."""
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from comrade import db
from comrade import picker
from comrade import render
from comrade import search as searcher
from comrade.config import config

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Quote Finder</title>
<style>
  :root { --bg: #fafaf8; --fg: #1d1d1b; --muted: #6b6b66; --card: #fff; --line: #e2e1dc; --accent: #b3261e; }
  @media (prefers-color-scheme: dark) {
    :root { --bg: #161615; --fg: #e8e7e3; --muted: #9a9993; --card: #1f1f1d; --line: #33322f; --accent: #f2766d; }
  }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--bg); color: var(--fg); font: 15px/1.5 system-ui, sans-serif; }
  main { max-width: 860px; margin: 0 auto; padding: 24px 16px 64px; }
  h1 { font-size: 20px; margin: 0 0 16px; }
  textarea, input { width: 100%; font: inherit; color: inherit; background: var(--card);
    border: 1px solid var(--line); border-radius: 6px; padding: 10px; }
  textarea { min-height: 140px; resize: vertical; }
  .row { display: flex; gap: 8px; margin: 8px 0 20px; flex-wrap: wrap; }
  .row input { flex: 1 1 180px; width: auto; }
  .row input[type=number] { flex: 0 0 90px; }
  button { font: inherit; padding: 9px 16px; border-radius: 6px; border: 1px solid var(--line);
    background: var(--card); color: inherit; cursor: pointer; }
  button.primary { background: var(--accent); border-color: var(--accent); color: #fff; }
  button:disabled { opacity: .5; cursor: wait; }
  .status { color: var(--muted); margin-bottom: 12px; min-height: 1.5em; }
  .card { background: var(--card); border: 1px solid var(--line); border-radius: 8px; padding: 14px 16px; margin-bottom: 12px; }
  .meta { color: var(--muted); font-size: 13px; margin-bottom: 6px; display: flex; gap: 12px; flex-wrap: wrap; }
  .cite { font-weight: 600; margin-top: 8px; }
  .cite a { color: inherit; }
  .quote { font-size: 16px; border-left: 3px solid var(--accent); padding-left: 12px; }
  .reason { color: var(--muted); font-size: 13px; margin-top: 6px; }
  .text { white-space: pre-wrap; }
  details summary { cursor: pointer; color: var(--muted); font-size: 13px; margin-top: 8px; }
  pre { white-space: pre-wrap; font-size: 13px; background: var(--bg); padding: 8px; border-radius: 4px; }
</style></head>
<body><main>
<h1>Quote Finder</h1>
<textarea id="text" placeholder="Paste a Reddit post or comment"></textarea>
<div class="row">
  <input id="author" placeholder="Author filter (optional), such as lenin">
  <input id="k" type="number" value="20" min="1" max="100" title="Number of quotes">
  <button id="search">Search</button>
  <button id="pick" class="primary">Pick quotes with the LLM</button>
</div>
<div class="status" id="status"></div>
<div id="results"></div>
</main>
<script>
const $ = id => document.getElementById(id);
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
function cite(p) {
  const title = p.source_url ? `<a href="${esc(p.source_url)}" target="_blank"><i>${esc(p.title)}</i></a>` : `<i>${esc(p.title)}</i>`;
  const section = p.section && p.section.toLowerCase() !== p.title.toLowerCase() ? `, ${esc(p.section)}` : "";
  return `${esc(p.author)}, ${title}${section}${p.year ? ` (${esc(p.year)})` : ""}`;
}
async function run(mode) {
  const text = $("text").value.trim();
  if (!text) return;
  for (const b of document.querySelectorAll("button")) b.disabled = true;
  $("status").textContent = mode === "pick" ? "Searching and asking the LLM..." : "Searching...";
  $("results").innerHTML = "";
  const t0 = performance.now();
  try {
    const r = await fetch("/api/" + mode, { method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({ text, author: $("author").value.trim() || null, k: +$("k").value || 20 }) });
    const data = await r.json();
    if (!r.ok) throw new Error(data.error);
    const secs = ((performance.now() - t0) / 1000).toFixed(1);
    $("status").textContent = `${data.results.length} results in ${secs} s` + (data.warning ? ` (${data.warning})` : "");
    $("results").innerHTML = data.results.map((p, i) => mode === "pick" ? `
      <div class="card">
        <div class="meta"><span>#${i + 1}</span><span>quote ${p.quote_id}</span></div>
        <div class="quote">${esc(p.quote)}</div>
        <div class="cite">${cite(p)}</div>
        <div class="reason">${esc(p.reason)}</div>
        <details><summary>Reddit reply</summary><pre>${esc(p.body)}</pre></details>
      </div>` : `
      <div class="card">
        <div class="meta"><span>#${i + 1}</span><span>score ${p.score.toFixed(4)}</span>
          <span>${p.rating == null ? "unrated" : "rated " + p.rating}</span><span>quote ${p.id}</span></div>
        <div class="quote">${esc(p.text)}</div>
        <div class="cite">${cite(p)}</div>
        ${p.summary ? `<div class="reason">${esc(p.summary)}</div>` : ""}
      </div>`).join("") || "<p>No results.</p>";
  } catch (e) {
    $("status").textContent = "Error: " + e.message;
  } finally {
    for (const b of document.querySelectorAll("button")) b.disabled = false;
  }
}
$("search").onclick = () => run("search");
$("pick").onclick = () => run("pick");
$("text").addEventListener("keydown", e => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) run("search"); });
</script>
</body></html>
"""

FIELDS = ("id", "author", "title", "section", "year", "source_url")


def _search(conn, body, warnings):
    return searcher.search(conn, body["text"], k=body.get("k"), author=body.get("author"), warn=warnings.append)


def handle_search(body):
    conn, warnings = db.connect(), []
    results = [{**{f: q.get(f) for f in FIELDS}, "text": q["text"], "score": q["score"],
                "rating": q["rating"], "summary": q["summary"]}
               for q in _search(conn, body, warnings)]
    return {"results": results, "warning": "; ".join(warnings)}


def handle_pick(body):
    if config.LLM_PROVIDER in ("cerebras", "anthropic") and not (config.LLM_API_KEY or os.getenv("ANTHROPIC_API_KEY")):
        raise RuntimeError(f"No API key for {config.LLM_PROVIDER}. Set LLM_API_KEY in .env, then restart the page.")
    conn, warnings = db.connect(), []
    candidates = _search(conn, {**body, "k": None}, warnings)  # the picker sees config.CANDIDATES quotes
    results = []
    for p in picker.pick(body["text"], candidates) if candidates else []:
        try:
            quote = render.quote_text(p["passage"], p["start"], p["end"])
        except render.Rejected as e:
            warnings.append(str(e))
            continue
        results.append({**{f: p["passage"].get(f) for f in FIELDS}, "quote": quote, "reason": p["reason"],
                        "quote_id": p["quote_id"],
                        "start": p["start"], "end": p["end"],
                        "body": render.render(p["passage"], p["start"], p["end"])})
    return {"results": results, "warning": "; ".join(warnings)}


ROUTES = {"/api/search": handle_search, "/api/pick": handle_pick}


class Handler(BaseHTTPRequestHandler):
    def _send(self, status, body, content_type="application/json"):
        data = body.encode() if isinstance(body, str) else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/":
            self._send(200, PAGE, "text/html")
        else:
            self._send(404, {"error": "Not found"})

    def do_POST(self):
        route = ROUTES.get(self.path)
        if not route:
            return self._send(404, {"error": "Not found"})
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or "{}")
            if not (body.get("text") or "").strip():
                return self._send(400, {"error": "Paste some text first."})
            self._send(200, route(body))
        except RuntimeError as e:
            self._send(500, {"error": str(e)})
        except Exception as e:
            self._send(500, {"error": f"{type(e).__name__}: {e}"})

    def log_message(self, fmt, *args):
        pass


def serve(host: str = "127.0.0.1", port: int = 8765):
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Open http://{host}:{port} (LLM: {config.LLM_PROVIDER} {config.LLM_MODEL}). Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
