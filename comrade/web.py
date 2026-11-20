"""A local test page: paste text, see ranked passages and the LLM's quote picks.

A second page, /key, is for rating the answer key in `eval/key.json`."""
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from comrade import answerkey
from comrade import db
from comrade import render
from comrade import reply
from comrade import search as searcher
from comrade.config import config

STYLE = """
  :root { --bg: #fafaf8; --fg: #1d1d1b; --muted: #6b6b66; --card: #fff; --line: #e2e1dc; --accent: #b3261e; }
  @media (prefers-color-scheme: dark) {
    :root { --bg: #161615; --fg: #e8e7e3; --muted: #9a9993; --card: #1f1f1d; --line: #33322f; --accent: #f2766d; }
  }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--bg); color: var(--fg); font: 15px/1.5 system-ui, sans-serif; }
  main { max-width: 860px; margin: 0 auto; padding: 24px 16px 64px; }
  nav { display: flex; gap: 16px; font-size: 20px; margin: 0 0 16px; }
  nav a { color: var(--muted); }
  textarea, input { width: 100%; font: inherit; color: inherit; background: var(--card);
    border: 1px solid var(--line); border-radius: 6px; padding: 10px; }
  textarea { min-height: 140px; resize: vertical; }
  .row { display: flex; gap: 8px; margin: 8px 0 20px; flex-wrap: wrap; }
  .row input { flex: 1 1 180px; width: auto; }
  .row input[type=number] { flex: 0 0 64px; width: 64px; }
  button { font: inherit; padding: 9px 16px; border-radius: 6px; border: 1px solid var(--line);
    background: var(--card); color: inherit; cursor: pointer; }
  button.primary { background: var(--accent); border-color: var(--accent); color: #fff; }
  button:disabled { opacity: .5; cursor: wait; }
  .status { color: var(--muted); margin-bottom: 12px; min-height: 1.5em; }
  .card { background: var(--card); border: 1px solid var(--line); border-radius: 8px; padding: 14px 16px; margin-bottom: 12px; }
  .meta { color: var(--muted); font-size: 13px; margin-bottom: 6px; display: flex; gap: 12px; flex-wrap: wrap; }
  .pager { display: flex; align-items: center; justify-content: center; gap: 12px; color: var(--muted); }
  .pager:empty { display: none; }
  .score { color: var(--accent); font-weight: 600; }
  .cite { font-weight: 600; margin-top: 8px; }
  .cite a { color: inherit; }
  .quote { font-size: 16px; border-left: 3px solid var(--accent); padding-left: 12px; }
  .reason { color: var(--muted); font-size: 13px; margin-top: 6px; }
  .text { white-space: pre-wrap; }
  details summary { cursor: pointer; color: var(--muted); font-size: 13px; margin-top: 8px; }
  .jobs { margin: 0 0 16px; display: grid; gap: 8px; }
  .jobs:empty { display: none; }
  .job { background: var(--card); border: 1px solid var(--line); border-radius: 8px; padding: 10px 14px; font-size: 13px; }
  .job .top { display: flex; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
  .job .name { font-weight: 600; }
  .job .detail { color: var(--muted); }
  .bar { height: 6px; background: var(--line); border-radius: 3px; margin-top: 8px; overflow: hidden; }
  .bar div { height: 100%; background: var(--accent); transition: width .5s; }
  pre { white-space: pre-wrap; font-size: 13px; background: var(--bg); padding: 8px; border-radius: 4px; }
"""

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Quote Finder</title>
<style>/*STYLE*/</style></head>
<body><main>
<nav><b>Quote finder</b><a href="/key">Answer key</a></nav>
<div class="jobs" id="jobs"></div>
<textarea id="text" placeholder="Paste a Reddit post or comment"></textarea>
<div class="row">
  <input id="author" placeholder="Author filter (optional), such as lenin">
  <button id="search">Search</button>
  <button id="pick" class="primary">Pick quotes with the LLM</button>
</div>
<div class="status" id="status"></div>
<div id="results"></div>
<div class="pager" id="pager"></div>
</main>
<script>
const $ = id => document.getElementById(id);
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
function cite(p) {
  const who = p.speaker ? `${esc(p.speaker)}, quoted in ${esc(p.author)}` : esc(p.author);
  const title = p.source_url ? `<a href="${esc(p.source_url)}" target="_blank"><i>${esc(p.title)}</i></a>` : `<i>${esc(p.title)}</i>`;
  const section = p.section && p.section.toLowerCase() !== p.title.toLowerCase() ? `, ${esc(p.section)}` : "";
  return `${who}, ${title}${section}${p.year ? ` (${esc(p.year)})` : ""}`;
}
const PAGE_SIZE = 10;
let found = [], page = 0;  // search results, paged in the browser
const pickCard = (p, i) => `
  <div class="card">
    <div class="meta"><span>${i ? "#" + (i + 1) + " runner-up" : "Best"}</span><span class="score">dunk ${p.score.toFixed(2)}</span><span>quote ${p.quote_id}</span></div>
    <div class="quote">${esc(p.quote)}</div>
    <div class="cite">${cite(p)}</div>
    <div class="reason">${esc(p.reason)}</div>
    <details><summary>Reddit reply</summary><pre>${esc(p.body)}</pre></details>
  </div>`;
const searchCard = (p, i) => `
  <div class="card">
    <div class="meta"><span>#${i + 1}</span><span>score ${p.score.toFixed(4)}</span>
      <span>${p.rating == null ? "unrated" : "rated " + p.rating}</span><span>quote ${p.id}</span></div>
    <div class="quote">${esc(p.text)}</div>
    <div class="cite">${cite(p)}</div>
    ${p.summary ? `<div class="reason">${esc(p.summary)}</div>` : ""}
  </div>`;
function showPage(n) {
  const pages = Math.ceil(found.length / PAGE_SIZE);
  page = Math.max(0, Math.min(n, pages - 1));
  const first = page * PAGE_SIZE;
  $("results").innerHTML = found.slice(first, first + PAGE_SIZE).map((p, i) => searchCard(p, first + i)).join("") || "<p>No results.</p>";
  $("pager").innerHTML = pages > 1 ? `
    <button id="prev" ${page ? "" : "disabled"}>Previous</button>
    <span>Page ${page + 1} of ${pages}</span>
    <button id="next" ${page < pages - 1 ? "" : "disabled"}>Next</button>` : "";
  if (pages > 1) {
    $("prev").onclick = () => { showPage(page - 1); scrollTo(0, $("results").offsetTop - 16); };
    $("next").onclick = () => { showPage(page + 1); scrollTo(0, $("results").offsetTop - 16); };
  }
}
async function run(mode) {
  const text = $("text").value.trim();
  if (!text) return;
  for (const b of document.querySelectorAll("button")) b.disabled = true;
  $("status").textContent = mode === "pick" ? "Searching and asking the LLM..." : "Searching...";
  $("results").innerHTML = $("pager").innerHTML = "";
  found = [];
  const t0 = performance.now();
  try {
    const r = await fetch("/api/" + mode, { method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({ text, author: $("author").value.trim() || null }) });
    const data = await r.json();
    if (!r.ok) throw new Error(data.error);
    const secs = ((performance.now() - t0) / 1000).toFixed(1);
    $("status").textContent = `${data.results.length} results in ${secs} s` + (data.warning ? ` (${data.warning})` : "")
      + (data.plan ? `. Claim: ${data.plan.claim} Tags: ${data.plan.tags.join(", ") || "none"}. Point: ${data.plan.point}` : "");
    if (mode === "pick") {
      $("results").innerHTML = data.results.map(pickCard).join("") || "<p>No results.</p>";
    } else {
      found = data.results;
      showPage(0);
    }
  } catch (e) {
    $("status").textContent = "Error: " + e.message;
  } finally {
    for (const b of document.querySelectorAll("button:not(#prev):not(#next)")) b.disabled = false;
  }
}
const seen = {};  // job name -> [time, done] from the first poll that saw it running, for the time estimate
function eta(job) {
  const now = Date.now() / 1000;
  if (!job.running) { delete seen[job.name]; return ""; }
  if (!seen[job.name] || seen[job.name][1] > job.done) seen[job.name] = [now, job.done];
  const [t0, d0] = seen[job.name], rate = (job.done - d0) / (now - t0);
  if (now - t0 < 10 || rate <= 0) return "measuring speed";
  const mins = (job.total - job.done) / rate / 60;
  return `${Math.round(rate * 60).toLocaleString()} per minute, about ${mins < 1 ? "1" : Math.round(mins)} min left`;
}
async function poll() {
  try {
    const jobs = (await (await fetch("/api/status")).json()).jobs;
    $("jobs").innerHTML = jobs.filter(j => j.running).map(j => {
      const pct = j.total ? Math.min(100, 100 * j.done / j.total) : 0;
      const state = eta(j);
      return `<div class="job"><div class="top"><span class="name">${esc(j.label)}</span>
        <span class="detail">${j.done.toLocaleString()} of ${j.total.toLocaleString()} (${pct.toFixed(1)}%) · ${esc(state)}</span></div>
        ${j.note ? `<div class="detail">${esc(j.note)}</div>` : ""}
        <div class="bar"><div style="width:${pct}%"></div></div></div>`;
    }).join("");
  } catch (e) { /* the page still works without the panel */ }
}
poll();
setInterval(poll, 5000);
$("search").onclick = () => run("search");
$("pick").onclick = () => run("pick");
$("text").addEventListener("keydown", e => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) run("search"); });
</script>
</body></html>
"""

PAGE = PAGE.replace("/*STYLE*/", STYLE)
KEY_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Answer Key</title>
<style>/*STYLE*/
  .post { font-size: 17px; border-left: 3px solid var(--muted); }
  .help { color: var(--muted); font-size: 13px; margin: 0 0 16px; }
  .grades { display: flex; gap: 8px; margin-top: 10px; }
  .grades button { padding: 5px 14px; font-size: 13px; }
  .grades button.on { background: var(--accent); border-color: var(--accent); color: #fff; }
  .card.cur { outline: 2px solid var(--accent); }
  .steps { display: flex; gap: 8px; align-items: center; margin: 0 0 16px; flex-wrap: wrap; color: var(--muted); }
  kbd { border: 1px solid var(--line); border-radius: 4px; padding: 0 5px; font: 12px ui-monospace, monospace; }
</style></head>
<body><main>
<nav><a href="/">Quote finder</a><b>Answer key</b></nav>
<p class="help">Rate how hard each quote dunks on the post, as a reply with no explanation.
<b>Dunk</b>: you'd be glad the bot posted it. <b>OK</b>: on point, but not sharp. <b>Miss</b>: it doesn't
answer the post, or it doesn't make sense alone. Keys: <kbd>1</kbd> Dunk, <kbd>2</kbd> OK, <kbd>3</kbd> Miss,
<kbd>j</kbd> <kbd>k</kbd> move between quotes, <kbd>n</kbd> <kbd>p</kbd> move between posts.</p>
<div class="steps">
  <button id="prev">Previous post</button><button id="next">Next post</button>
  <button id="todo" class="primary">Next unrated</button><span id="progress"></span>
</div>
<div id="post"></div>
<div id="pool"></div>
<details><summary>Add a post</summary>
  <textarea id="text" placeholder="Paste a Reddit post or comment"></textarea>
  <div class="row"><button id="add">Add it and find quotes</button><span class="status" id="status"></span></div>
</details>
</main>
<script>
const $ = id => document.getElementById(id);
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const NAMES = ["Miss", "OK", "Dunk"];
let key = {posts: [], grades: {}}, at = 0, cur = 0;
const grades = () => key.grades[key.posts[at].id] ??= {};
const unrated = post => post.pool.filter(q => (key.grades[post.id] ?? {})[q.hash] == null).length;
function draw() {
  const post = key.posts[at];
  if (!post) { $("post").innerHTML = "<p>No posts yet. Add one below.</p>"; $("pool").innerHTML = ""; return; }
  const left = key.posts.reduce((n, p) => n + unrated(p), 0), all = key.posts.reduce((n, p) => n + p.pool.length, 0);
  $("progress").textContent = `Post ${at + 1} of ${key.posts.length} · ${all - left} of ${all} quotes rated`;
  $("prev").disabled = at === 0;
  $("next").disabled = at === key.posts.length - 1;
  $("todo").disabled = left === 0;
  $("post").innerHTML = `<div class="card"><div class="meta"><span>The post</span></div><div class="quote post">${esc(post.text)}</div></div>`;
  $("pool").innerHTML = post.pool.map((q, i) => `
    <div class="card ${i === cur ? "cur" : ""}" data-i="${i}">
      <div class="quote">${esc(q.text)}</div>
      <div class="cite">${q.speaker ? esc(q.speaker) + ", quoted in " : ""}${esc(q.author)}, <i>${esc(q.title)}</i>${q.year ? ` (${esc(q.year)})` : ""}</div>
      <div class="grades">${[2, 1, 0].map(g =>
        `<button data-g="${g}" class="${grades()[q.hash] === g ? "on" : ""}">${NAMES[g]}</button>`).join("")}</div>
    </div>`).join("") || "<p>No quotes found for this post.</p>";
}
function go(post, quote) {
  at = Math.max(0, Math.min(post, key.posts.length - 1));
  const pool = key.posts[at]?.pool ?? [];
  const first = pool.findIndex(q => grades()[q.hash] == null);
  cur = Math.max(0, Math.min(quote ?? (first < 0 ? 0 : first), pool.length - 1));
  draw();
  document.querySelector(".card.cur")?.scrollIntoView({block: "nearest"});
}
function nextUnrated() {
  for (let n = 0; n < key.posts.length; n++) {
    const i = (at + n) % key.posts.length;
    if (unrated(key.posts[i])) return go(i);
  }
  draw();
}
async function send(path, body) {
  const r = await fetch(path, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
  const data = await r.json();
  if (!r.ok) throw new Error(data.error);
  return data;
}
async function rate(i, g) {
  const post = key.posts[at], q = post.pool[i];
  if (!q) return;
  const value = grades()[q.hash] === g ? null : g;  // click the same grade again to clear it
  try {
    await send("/api/key/grade", {post: post.id, hash: q.hash, grade: value});
  } catch (e) { alert("Not saved: " + e.message); return; }
  if (value == null) delete grades()[q.hash]; else grades()[q.hash] = value;
  const later = post.pool.findIndex((x, n) => n > i && grades()[x.hash] == null);
  if (value != null && later < 0 && !unrated(post)) return nextUnrated();
  go(at, value == null ? i : later < 0 ? post.pool.findIndex(x => grades()[x.hash] == null) : later);
}
$("pool").onclick = e => {
  const card = e.target.closest(".card");
  if (!card) return;
  if (e.target.dataset.g != null) rate(+card.dataset.i, +e.target.dataset.g);
  else go(at, +card.dataset.i);
};
$("prev").onclick = () => go(at - 1);
$("next").onclick = () => go(at + 1);
$("todo").onclick = nextUnrated;
document.addEventListener("keydown", e => {
  if (e.target.tagName === "TEXTAREA" || e.metaKey || e.ctrlKey || e.altKey) return;
  if ("123".includes(e.key)) rate(cur, 3 - e.key);  // 1 is Dunk, matching the button order
  else if (e.key === "j") go(at, cur + 1);
  else if (e.key === "k") go(at, cur - 1);
  else if (e.key === "n") go(at + 1);
  else if (e.key === "p") go(at - 1);
});
$("add").onclick = async () => {
  const text = $("text").value.trim();
  if (!text) return;
  $("add").disabled = true;
  $("status").textContent = "Finding quotes...";
  try {
    key.posts.push(await send("/api/key/post", {text}));
    $("text").value = $("status").textContent = "";
    go(key.posts.length - 1);
  } catch (e) { $("status").textContent = "Error: " + e.message; }
  $("add").disabled = false;
};
fetch("/api/key").then(r => r.json()).then(data => { key = data; nextUnrated(); });
</script>
</body></html>
""".replace("/*STYLE*/", STYLE)

SEARCH_RESULTS = 100  # the page shows these 10 at a time

FIELDS = ("id", "author", "title", "section", "year", "source_url")


def _search(conn, body, warnings):
    return searcher.search(conn, body["text"], k=body.get("k") or SEARCH_RESULTS, author=body.get("author"),
                           warn=warnings.append)


def handle_search(body):
    conn, warnings = db.connect(), []
    results = [{**{f: q.get(f) for f in FIELDS}, "text": q["text"], "score": q["score"],
                "rating": q["rating"], "summary": q["summary"]}
               for q in _search(conn, body, warnings)]
    return {"results": results, "warning": "; ".join(warnings)}


def handle_pick(body):
    if config.LLM_PROVIDER in ("cerebras", "anthropic") and not (config.LLM_API_KEY or os.getenv("ANTHROPIC_API_KEY")):
        raise RuntimeError(f"No API key for {config.LLM_PROVIDER}. Set LLM_API_KEY in .env, then restart the page.")
    warnings = []
    found = reply.find(db.connect(), body["text"], body.get("author"), warn=warnings.append)
    results = []
    for p in found["picks"]:
        try:
            quote = render.quote_text(p["passage"], p["start"], p["end"])
        except render.Rejected as e:
            warnings.append(str(e))
            continue
        results.append({**{f: p["passage"].get(f) for f in FIELDS}, "quote": quote, "reason": p["reason"], "score": p["score"],
                        "quote_id": p["quote_id"],
                        "start": p["start"], "end": p["end"],
                        "speaker": p.get("speaker"),
                        "body": render.render(p["passage"], p["start"], p["end"], p.get("speaker"))})
    return {"results": results, "plan": found["plan"], "warning": "; ".join(warnings)}


def _running(command: str) -> bool:
    import subprocess
    return subprocess.run(["pgrep", "-f", f"python.*main.py {command}"], capture_output=True).returncode == 0


_status_cache = {"at": 0.0, "body": None}


def handle_status():
    """Progress of the background jobs that build the quote index."""
    import time

    from comrade import quotes
    if time.time() - _status_cache["at"] < 4:
        return _status_cache["body"]
    conn = db.connect()
    one = lambda sql, *a: conn.execute(sql, a).fetchone()[0]
    model = config.LLM_MODEL
    jobs = [
        {"name": "rate", "label": "Rating quotes",
         "done": one("SELECT COUNT(*) FROM rated_paragraphs WHERE model = ?", quotes._rated_key()),
         "total": one("SELECT COUNT(DISTINCT paragraph_id) FROM quotes")},
        {"name": "rate --long", "label": "Finding longer quotes",
         "done": one("SELECT COUNT(*) FROM rated_paragraphs WHERE model = ?", quotes._rated_key(long=True)),
         "total": one("""SELECT COUNT(*) FROM paragraphs p WHERE LENGTH(p.text) > ?
                         AND p.id IN (SELECT paragraph_id FROM quotes)
                         AND (SELECT COUNT(*) FROM sentences s WHERE s.paragraph_id = p.id) >= 3""",
                      config.MAX_QUOTE_CHARS)},
        {"name": "embed", "label": "Embedding quotes and summaries",
         "done": one("SELECT COUNT(*) FROM quote_vectors WHERE model = ?", config.EMBED_MODEL),
         "total": one("SELECT COUNT(DISTINCT hash) FROM quotes")
                  + one("SELECT COUNT(*) FROM quote_scores WHERE summary <> ''")},
        {"name": "tag", "label": "Tagging the quote bank",
         "done": one("SELECT COUNT(*) FROM bank_reviewed WHERE model = ?", model),
         "total": one("SELECT COUNT(*) FROM quote_scores WHERE rating >= 3")},
    ]
    attributed = {h: own for h, own in conn.execute("SELECT hash, own FROM attributions WHERE model = ?", (model,))}
    texts = [quotes.text_hash(t) for (t,) in conn.execute("SELECT text FROM bank")]
    done = sum(h in attributed for h in texts)
    others = sum(1 for own in attributed.values() if not own)
    jobs.append({"name": "attribute", "label": "Checking who said each quote", "done": done, "total": len(texts),
                 "note": f"{others:,} so far are someone else's words"})
    for j in jobs:
        j["running"] = _running(j["name"]) and not (j["name"] == "rate" and _running("rate --long"))
        j["done"] = min(j["done"], j["total"])
    _status_cache.update(at=time.time(), body={"jobs": jobs})
    return _status_cache["body"]


def handle_grade(body):
    answerkey.grade(body.get("post"), body.get("hash"), body.get("grade"))
    return {"ok": True}


ROUTES = {"/api/search": handle_search, "/api/pick": handle_pick,
          "/api/key/post": lambda body: answerkey.add_post(body["text"].strip()), "/api/key/grade": handle_grade}
NEEDS_TEXT = {"/api/search", "/api/pick", "/api/key/post"}
PAGES = {"/": PAGE, "/key": KEY_PAGE}
GETS = {"/api/status": handle_status, "/api/key": answerkey.load}


class Handler(BaseHTTPRequestHandler):
    def _send(self, status, body, content_type="application/json"):
        data = body.encode() if isinstance(body, str) else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path in PAGES:
            self._send(200, PAGES[self.path], "text/html")
        elif self.path in GETS:
            try:
                self._send(200, GETS[self.path]())
            except Exception as e:
                self._send(500, {"error": f"{type(e).__name__}: {e}"})
        else:
            self._send(404, {"error": "Not found"})

    def do_POST(self):
        route = ROUTES.get(self.path)
        if not route:
            return self._send(404, {"error": "Not found"})
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or "{}")
            if self.path in NEEDS_TEXT and not (body.get("text") or "").strip():
                return self._send(400, {"error": "Paste some text first."})
            self._send(200, route(body))
        except ValueError as e:
            self._send(400, {"error": str(e)})
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
