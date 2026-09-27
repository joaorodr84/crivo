"use strict";

// Everything that came from Pixabay (names, tags, URLs) is placed with textContent or
// checked before it becomes an attribute; nothing here builds HTML from a string.

const token = new URLSearchParams(location.search).get("t") || "";
const $ = (id) => document.getElementById(id);
const list = $("list");

let snapshot = null;
const busy = new Set();        // keywords whose request is in flight
const drafts = new Map();      // what has been typed in each keyword's search box
const nodes = new Map();       // label -> {node, signature}

function el(tag, attrs, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (value === true) node.setAttribute(key, "");
    else if (value !== false && value != null) node.setAttribute(key, value);
  }
  for (const child of children) {
    if (child != null) node.append(child);
  }
  return node;
}

const safeHttps = (url) => typeof url === "string" && url.startsWith("https://");
const safeImage = (url) =>
  safeHttps(url) || (typeof url === "string" && url.startsWith("data:image/"));

async function api(path, body) {
  const headers = { "X-Crivo-Token": token };
  const options = { headers };
  if (body !== undefined) {
    options.method = "POST";
    headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  let response;
  try {
    response = await fetch(path, options);
  } catch (err) {
    throw new Error("Crivo has stopped, so there is nothing to talk to. Close this tab.");
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || response.statusText);
  return data;
}

let toastTimer = null;
function flash(message) {
  const toast = $("toast");
  toast.textContent = message;
  toast.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { toast.hidden = true; }, 7000);
}

async function act(label, request) {
  busy.add(label);
  render();
  try {
    snapshot = await request();
  } catch (err) {
    flash(err.message);
  } finally {
    busy.delete(label);
    render();
  }
}

const post = (path, body) => () => api(path, body);

function card(k, c) {
  const selected = k.picked.includes(c.id);
  const source = [c.webformat_url, c.preview_url].find(safeImage);
  const image = el("img", {
    src: source,
    alt: c.tags.length ? c.tags.join(", ") : k.label,
    loading: "lazy",
    referrerpolicy: "no-referrer",
  });
  const thumb = el("button", {
    type: "button", class: "thumb", "aria-pressed": String(selected),
    title: selected ? "Click to un-pick" : "Click to pick this image",
    disabled: busy.has(k.label),
    onclick: () => act(k.label, post("/api/pick", { label: k.label, id: c.id, selected: !selected })),
  }, image, el("span", { class: "check", "aria-hidden": "true" }, "✓"));
  const link = safeHttps(c.page_url)
    ? el("a", { href: c.page_url, target: "_blank", rel: "noopener noreferrer" }, "Pixabay ↗")
    : null;
  return el("figure", { class: "card" + (selected ? " selected" : "") },
    thumb,
    el("figcaption", {},
      el("span", { class: "by" }, "by " + (c.user || "unknown")),
      link,
      c.tags.length ? el("span", { class: "tags" }, c.tags.join(", ")) : null));
}

function chip(k) {
  if (k.picked.length) return el("span", { class: "chip ok" }, k.picked.length + " picked");
  if (k.skipped) return el("span", { class: "chip" }, "skipped");
  return el("span", { class: "chip todo" }, "needs a decision");
}

function searchForm(k) {
  const input = el("input", {
    type: "text", maxlength: "100", "aria-label": "Search term for " + k.label,
    placeholder: "Search for something else", value: drafts.get(k.label) || "",
    disabled: busy.has(k.label),
    oninput: () => drafts.set(k.label, input.value),
  });
  return el("form", {
    onsubmit: (event) => {
      event.preventDefault();
      const term = input.value.trim();
      if (!term) { input.focus(); return; }
      drafts.delete(k.label);
      act(k.label, post("/api/retry", { label: k.label, term }));
    },
  }, input, el("button", { type: "submit", disabled: busy.has(k.label) }, "Search"));
}

function section(k) {
  const isBusy = busy.has(k.label);
  const parts = [
    el("div", { class: "kw-head" }, el("h2", {}, k.label), chip(k)),
    k.query ? el("p", { class: "query" }, "Searched for: “" + k.query + "”") : null,
  ];
  if (isBusy) parts.push(el("p", { class: "msg" }, "Searching…"));
  if (k.error) parts.push(el("p", { class: "msg error" }, k.error));
  if (k.notice) parts.push(el("p", { class: "msg notice" }, k.notice));
  if (k.status === "empty") {
    parts.push(el("p", { class: "msg notice" }, "Pixabay has nothing for this search. Try another term."));
  }
  const waiting = k.status === "pending" && snapshot.phase === "searching";
  if (waiting) parts.push(el("p", { class: "msg" }, "Waiting to be searched\u2026"));
  else if (k.status === "pending") parts.push(el("p", { class: "msg notice" }, "Not searched yet."));
  if (k.candidates.length) {
    parts.push(el("div", { class: "grid" }, ...k.candidates.map((c) => card(k, c))));
  }

  if (waiting) {
    return el("section", { class: "kw" }, ...parts);  // nothing to decide until it is searched
  }
  const actions = [];
  if (k.status === "failed" || k.status === "pending") {
    actions.push(el("button", {
      type: "button", class: "primary", disabled: isBusy,
      onclick: () => act(k.label, post("/api/retry", { label: k.label })),
    }, k.status === "pending" ? "Search" : "Try again"));
  }
  if (k.has_more) {
    actions.push(el("button", {
      type: "button", disabled: isBusy,
      onclick: () => act(k.label, post("/api/retry", { label: k.label, next_page: true })),
    }, "More results"));
  }
  actions.push(el("button", {
    type: "button", disabled: isBusy,
    onclick: () => act(k.label, post("/api/skip", { label: k.label, skipped: !k.skipped })),
  }, k.skipped ? "Undo skip" : "Skip this keyword"));
  actions.push(searchForm(k));
  parts.push(el("div", { class: "actions" }, ...actions));

  return el("section", {
    class: "kw" + (k.done ? " done" : "") + (k.skipped ? " skipped" : ""),
  }, ...parts);
}

const signature = (k) => JSON.stringify([k, busy.has(k.label), snapshot.phase]);

function renderKeyword(k) {
  const entry = nodes.get(k.label);
  const sig = signature(k);
  if (entry && entry.signature === sig) return;
  const fresh = section(k);
  if (entry) entry.node.replaceWith(fresh);
  else list.append(fresh);
  nodes.set(k.label, { node: fresh, signature: sig });
}

function render() {
  if (!snapshot) return;
  const s = snapshot;
  const searching = s.phase === "searching";
  if (s.phase === "start") {
    $("progress-text").textContent = "Paste your keywords to begin";
    $("progress-fill").style.width = "0%";
  } else if (searching) {
    $("progress-text").textContent =
      `Searching Pixabay: ${s.searching.done} of ${s.searching.total}\u2026`;
    $("progress-fill").style.width =
      (s.searching.total ? (100 * s.searching.done) / s.searching.total : 0) + "%";
  } else {
    $("progress-text").textContent =
      `${s.done} of ${s.total} keywords done \u00b7 ${s.picks} image${s.picks === 1 ? "" : "s"} picked`;
    $("progress-fill").style.width = (s.total ? (100 * s.done) / s.total : 0) + "%";
  }
  const finish = $("finish");
  finish.textContent = `Finish (${s.picks})`;
  finish.disabled = s.picks === 0 || s.finished || busy.size > 0 || s.phase !== "picking";
  finish.hidden = s.phase === "start";
  const banner = $("banner");
  const words = [...(s.notes || []), s.stopped].filter(Boolean).join(" ");
  banner.hidden = !words;
  banner.textContent = words;
  $("start").hidden = s.phase !== "start" || s.finished;
  syncPolling(searching);

  if (s.finished) {
    list.hidden = true;
    $("done").hidden = false;
    return;
  }
  // Only sections whose data changed are rebuilt, so a click in one keyword does not
  // reload every thumbnail or move the scroll position.
  s.keywords.forEach(renderKeyword);
}

// While the search runs in the background the page asks how far it has got. It stops asking
// the moment the search is over, so an idle page costs the server nothing.
let pollTimer = null;
function syncPolling(searching) {
  if (searching && pollTimer === null) {
    pollTimer = setInterval(async () => {
      try {
        const data = await api("/api/state");
        if (busy.size === 0) { snapshot = data; render(); }
      } catch (err) { /* the next tick tries again; a dead server shows on the next click */ }
    }, 1000);
  } else if (!searching && pollTimer !== null) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

$("start-button").addEventListener("click", async () => {
  const text = $("start-text").value;
  const problem = $("start-error");
  problem.textContent = "";
  if (!text.trim()) { problem.textContent = "Type or paste at least one keyword."; return; }
  $("start-button").disabled = true;
  try {
    snapshot = await api("/api/start", { text });
    render();
  } catch (err) {
    problem.textContent = err.message;
  } finally {
    $("start-button").disabled = false;
  }
});

$("finish").addEventListener("click", async () => {
  const undecided = snapshot.total - snapshot.done;
  if (undecided > 0) {
    const noun = undecided === 1 ? "keyword has" : "keywords have";
    const verb = undecided === 1 ? "is" : "are";
    const ok = window.confirm(
      `${undecided} ${noun} no pick and ${verb} not skipped, so will be left out. Finish anyway?`);
    if (!ok) return;
  }
  try {
    snapshot = await api("/api/finish", {});
  } catch (err) {
    flash(err.message);
  }
  render();
});

api("/api/state").then((data) => { snapshot = data; render(); }).catch((err) => {
  $("progress-text").textContent = err.message;
});
