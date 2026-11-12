/* Netviber dashboard UI: vanilla JS, no build step. */

const $ = (sel) => document.querySelector(sel);

const REFRESH_KEY = "netviber.refreshMinutes";
const TAB_KEY = "netviber.activeTab";
const READ_KEY = "netviber.read";
const POLL_MS = 1500;
const READ_SYNC_MS = 5000; // cross-device read-state sync poll
const READ_MAX_AGE = 30 * 24 * 3600; // forget read state after 30 days (seconds)
const READ_MAX_ITEMS = 10000;
const PAGE_SIZE = 10; // headlines per tile page

let state = { tabs: [], feeds: [], cache: {}, read: {} };
let watching = false;
let autoTimer = null;
let readSet = loadReadSet(); // merged read marks; server is source of truth
let pendingKeys = new Set(); // read marks queued for the server
let flushTimer = null;
const pageMap = {}; // feedId -> current page index (0 = newest)

const el = {
  topbar: $("#topbar"),
  tabs: $("#tabs"),
  grid: $("#grid"),
  importScreen: $("#import-screen"),
  dashboard: $("#dashboard"),
  fileInput: $("#file-input"),
  importBtn: $("#import-btn"),
  refreshAll: $("#refresh-all"),
  interval: $("#refresh-interval"),
  toast: $("#toast"),
};

init();

async function init() {
  el.importBtn.addEventListener("click", () => el.fileInput.click());
  el.fileInput.addEventListener("change", () => {
    const file = el.fileInput.files[0];
    if (file) importFile(file);
    el.fileInput.value = "";
  });
  el.refreshAll.addEventListener("click", () => startRefresh());

  el.interval.value = localStorage.getItem(REFRESH_KEY) ?? "15";
  el.interval.addEventListener("change", () => {
    localStorage.setItem(REFRESH_KEY, el.interval.value);
    scheduleAuto();
  });

  setupDropzone();

  await loadState();
  migrateReadSet();
  setInterval(syncReadSet, READ_SYNC_MS);
  scheduleAuto();
  refreshIfStale();
}

/* ---------- read state ---------- */

/* Read marks live on the server (shared across devices); this localStorage
   copy is an offline cache and the migration source for pre-existing
   browser-local marks. Timestamps are unix seconds. */

function loadReadSet() {
  try {
    const raw = JSON.parse(localStorage.getItem(READ_KEY) ?? "{}");
    const cutoff = Date.now() / 1000 - READ_MAX_AGE;
    const clean = {};
    for (const [key, ts] of Object.entries(raw)) {
      if (typeof ts === "number" && ts > cutoff) clean[key] = ts;
    }
    return clean;
  } catch {
    return {};
  }
}

function saveReadSet() {
  const entries = Object.entries(readSet);
  if (entries.length > READ_MAX_ITEMS) {
    entries.sort((a, b) => b[1] - a[1]);
    readSet = Object.fromEntries(entries.slice(0, READ_MAX_ITEMS));
  }
  localStorage.setItem(READ_KEY, JSON.stringify(readSet));
}

/* Merge the server's read set with any browser-local marks; push local-only
   marks (pre-existing or made offline) to the server. One-time migration +
   catch-up. */
function migrateReadSet() {
  const server = state.read ?? {};
  const local = loadReadSet();
  const missing = Object.keys(local).filter((k) => !Object.hasOwn(server, k));
  readSet = { ...server, ...local };
  if (missing.length) {
    pushReadMarks(missing);
  }
  saveReadSet();
}

function itemKey(item) {
  return item.id || item.link || item.title;
}

function isRead(item) {
  return Object.hasOwn(readSet, itemKey(item));
}

function markRead(key) {
  readSet[key] = Math.floor(Date.now() / 1000);
  saveReadSet();
  pendingKeys.add(key);
  clearTimeout(flushTimer);
  flushTimer = setTimeout(flushPending, 500);
}

function pushReadMarks(keys) {
  fetch("/api/read", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(keys),
  })
    .then((res) => {
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
    })
    .catch(() => {
      // Offline or hiccup: keep them queued for the next sync.
      for (const key of keys) pendingKeys.add(key);
    });
}

function flushPending() {
  if (!pendingKeys.size) return;
  const keys = [...pendingKeys];
  pendingKeys.clear();
  pushReadMarks(keys);
}

/* Pull read marks from the server (another device may have added some);
   flush any locally queued ones first. Read-only: no deletions. */
async function syncReadSet() {
  flushPending();
  try {
    const res = await fetch("/api/read");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const { read } = await res.json();
    let changed = false;
    for (const [key, ts] of Object.entries(read)) {
      if (!Object.hasOwn(readSet, key)) {
        readSet[key] = ts;
        changed = true;
      }
    }
    if (changed) {
      renderGrid();
      renderTabs();
    }
  } catch {
    // Offline; local cache still tracks marks made here.
  }
}

function unreadCount(feed) {
  const cache = state.cache[feed.id];
  return (cache?.items ?? []).filter((item) => !isRead(item)).length;
}

/* ---------- data ---------- */

async function loadState() {
  try {
    const res = await fetch("/api/state");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    state = await res.json();
  } catch {
    toast("Could not reach the Netviber server");
    return;
  }
  render();
}

function refreshIfStale() {
  const mins = parseInt(el.interval.value, 10) || 0;
  const stale = state.feeds.some((feed) => {
    const cache = state.cache[feed.id];
    if (!cache?.fetched_at) return true;
    return mins > 0 && Date.now() - new Date(cache.fetched_at) > mins * 60000;
  });
  if (stale && state.feeds.length) startRefresh();
}

function startRefresh(feedId) {
  const url = feedId
    ? `/api/refresh?feed_id=${encodeURIComponent(feedId)}`
    : "/api/refresh";
  fetch(url, { method: "POST" })
    .then((res) => {
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
    })
    .then(watch)
    .catch(() => toast("Refresh failed — is the server running?"));
}

async function watch() {
  if (watching) return;
  watching = true;
  el.refreshAll.classList.add("busy");
  try {
    while (true) {
      await sleep(POLL_MS);
      const res = await fetch("/api/state");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      state = await res.json();
      renderGrid();
      if (!Object.values(state.cache).some((c) => c.status === "pending")) break;
    }
  } catch {
    toast("Lost contact with the server while refreshing");
  } finally {
    watching = false;
    el.refreshAll.classList.remove("busy");
    render();
  }
}

function scheduleAuto() {
  clearInterval(autoTimer);
  autoTimer = null;
  const mins = parseInt(el.interval.value, 10);
  if (mins > 0) {
    autoTimer = setInterval(() => {
      if (!watching) startRefresh();
    }, mins * 60 * 1000);
  }
}

async function importFile(file) {
  if (state.feeds.length &&
      !confirm("Replace your current tabs and feeds with this OPML file?")) {
    return;
  }
  const form = new FormData();
  form.append("file", file, file.name);
  try {
    const res = await fetch("/api/import", { method: "POST", body: form });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || `HTTP ${res.status}`);
    }
    state = await res.json();
    localStorage.removeItem(TAB_KEY);
    render();
    watch();
    toast(`Imported ${state.feeds.length} feeds`);
  } catch (err) {
    toast(`Import failed: ${err.message}`);
  }
}

/* ---------- rendering ---------- */

function render() {
  const hasFeeds = state.feeds.length > 0;
  el.topbar.hidden = !hasFeeds;
  el.importScreen.hidden = hasFeeds;
  el.dashboard.hidden = !hasFeeds;
  if (hasFeeds) {
    renderTabs();
    renderGrid();
  }
}

function renderTabs() {
  el.tabs.replaceChildren();
  const active = activeTabId();
  for (const tab of state.tabs) {
    const feeds = state.feeds.filter((f) => f.tab_id === tab.id);
    const unread = feeds.reduce((sum, f) => sum + unreadCount(f), 0);
    const btn = document.createElement("button");
    btn.className = "tab" + (tab.id === active ? " active" : "");
    btn.setAttribute("role", "tab");
    btn.textContent = tab.title;
    if (unread > 0) {
      const badge = document.createElement("span");
      badge.className = "count";
      badge.textContent = unread;
      badge.title = `${unread} unread in this tab`;
      btn.append(badge);
    }
    btn.addEventListener("click", () => {
      localStorage.setItem(TAB_KEY, tab.id);
      renderTabs();
      renderGrid();
    });
    el.tabs.append(btn);
  }
}

function activeTabId() {
  const saved = localStorage.getItem(TAB_KEY);
  if (saved && state.tabs.some((t) => t.id === saved)) return saved;
  return state.tabs[0]?.id ?? null;
}

function renderGrid() {
  const feeds = state.feeds.filter((f) => f.tab_id === activeTabId());
  el.grid.replaceChildren();
  if (!feeds.length) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = "No feeds in this tab.";
    el.grid.append(empty);
    return;
  }
  for (const feed of feeds) el.grid.append(tile(feed));
}

function tile(feed) {
  const cache = state.cache[feed.id] ?? {};
  const tile = document.createElement("article");
  tile.className = "tile";
  if (cache.status === "error") tile.classList.add("error");

  const head = document.createElement("header");
  head.append(favicon(feed));
  const titles = document.createElement("div");
  titles.className = "titles";
  const link = document.createElement("a");
  link.className = "feed-link";
  link.href = feedHome(feed);
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  link.title = `Open ${feed.title} — ${link.href}`;
  const name = document.createElement("h2");
  name.textContent = feed.title;
  link.append(name);
  const host = document.createElement("span");
  host.className = "host";
  host.textContent = hostOf(feed.html_url || feed.xml_url);
  titles.append(link, host);
  head.append(titles);

  const actions = document.createElement("div");
  actions.className = "tile-actions";
  const spin = document.createElement("span");
  spin.className = "spinner";
  spin.hidden = cache.status !== "pending";
  const markAll = document.createElement("button");
  markAll.className = "icon-btn";
  markAll.title = "Mark all read";
  markAll.textContent = "✓";
  markAll.addEventListener("click", () => {
    for (const item of cache.items ?? []) markRead(itemKey(item));
    renderGrid();
    renderTabs();
  });
  const btn = document.createElement("button");
  btn.className = "icon-btn";
  btn.title = "Refresh this feed";
  btn.textContent = "↻";
  btn.addEventListener("click", () => startRefresh(feed.id));
  actions.append(spin, markAll, btn);
  head.append(actions);
  tile.append(head);

  if (cache.status === "error") {
    const err = document.createElement("p");
    err.className = "tile-error";
    err.textContent = `⚠ ${cache.error || "Failed to load"}`;
    tile.append(err);
  }

  const list = document.createElement("ul");
  list.className = "items";
  const items = cache.items ?? [];
  const pages = Math.max(1, Math.ceil(items.length / PAGE_SIZE));
  pageMap[feed.id] = Math.min(pageMap[feed.id] ?? 0, pages - 1);
  const page = pageMap[feed.id];
  const visible = items.slice(page * PAGE_SIZE, page * PAGE_SIZE + PAGE_SIZE);
  if (!items.length && cache.status !== "pending") {
    const li = document.createElement("li");
    li.className = "muted";
    li.textContent = "No headlines yet.";
    list.append(li);
  }
  for (const item of visible) {
    const li = document.createElement("li");
    const unread = !isRead(item);
    li.className = unread ? "unread" : "read";
    if (item.link) {
      const a = document.createElement("a");
      a.href = item.link;
      a.target = "_blank";
      a.rel = "noopener noreferrer";
      a.textContent = item.title;
      a.addEventListener("click", () => {
        markRead(itemKey(item));
        li.classList.remove("unread");
        li.classList.add("read");
        updateFoot();
        renderTabs();
      });
      li.append(a);
    } else {
      li.textContent = item.title;
    }
    const time = document.createElement("span");
    time.className = "when";
    if (item.published) {
      const ms = new Date(item.published).getTime();
      time.textContent = timeAgo(item.published);
      if (!Number.isNaN(ms)) time.title = localDateTime(ms);
    }
    li.append(time);
    list.append(li);
  }
  tile.append(list);

  if (pages > 1) {
    const pager = document.createElement("div");
    pager.className = "pager";
    const prev = document.createElement("button");
    prev.className = "icon-btn";
    prev.textContent = "‹ newer";
    prev.title = "Show newer headlines";
    prev.disabled = page === 0;
    prev.addEventListener("click", () => {
      pageMap[feed.id] = page - 1;
      renderGrid();
    });
    const label = document.createElement("span");
    label.className = "pager-label";
    label.textContent = `${page + 1} / ${pages}`;
    const next = document.createElement("button");
    next.className = "icon-btn";
    next.textContent = "older ›";
    next.title = "Show older headlines";
    next.disabled = page >= pages - 1;
    next.addEventListener("click", () => {
      pageMap[feed.id] = page + 1;
      renderGrid();
    });
    pager.append(prev, label, next);
    tile.append(pager);
  }

  const foot = document.createElement("footer");
  function updateFoot() {
    const count = unreadCount(feed);
    if (cache.fetched_at) {
      const ms = new Date(cache.fetched_at).getTime();
      foot.textContent = `updated ${timeAgo(cache.fetched_at)}`;
      if (count > 0) foot.textContent += ` · ${count} unread`;
      if (!Number.isNaN(ms)) foot.title = localDateTime(ms);
    } else {
      foot.textContent = "never fetched";
    }
  }
  updateFoot();
  tile.append(foot);
  return tile;
}

/* ---------- helpers ---------- */

function favicon(feed) {
  const host = hostOf(feed.html_url || feed.xml_url);
  const wrap = document.createElement("span");
  wrap.className = "fav";
  if (host) {
    const img = document.createElement("img");
    img.src = `https://www.google.com/s2/favicons?sz=32&domain=${encodeURIComponent(host)}`;
    img.alt = "";
    img.loading = "lazy";
    img.addEventListener(
      "error",
      () => wrap.replaceChildren(letterAvatar(feed.title)),
      { once: true }
    );
    wrap.append(img);
  } else {
    wrap.append(letterAvatar(feed.title));
  }
  return wrap;
}

const COLORS = [
  "#2dd4bf", "#60a5fa", "#f472b6", "#facc15",
  "#a78bfa", "#fb923c", "#4ade80",
];

function letterAvatar(text) {
  const span = document.createElement("span");
  span.className = "avatar";
  span.textContent = (text.trim()[0] || "?").toUpperCase();
  span.style.background = colorFor(text);
  return span;
}

function colorFor(text) {
  let h = 0;
  for (const ch of text) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return COLORS[h % COLORS.length];
}

function hostOf(url) {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return "";
  }
}

/* The feed's main page: the OPML htmlUrl if present, otherwise the
   site root of the feed URL. */
function feedHome(feed) {
  const url = feed.html_url || feed.xml_url;
  try {
    const parsed = new URL(url);
    return parsed.origin + "/";
  } catch {
    return url;
  }
}

function timeAgo(iso) {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";
  const secs = Math.max(0, (Date.now() - then) / 1000);
  if (secs < 60) return "just now";
  const mins = secs / 60;
  if (mins < 60) return `${Math.floor(mins)}m ago`;
  const hours = mins / 60;
  if (hours < 24) return `${Math.floor(hours)}h ago`;
  const days = hours / 24;
  if (days < 7) return `${Math.floor(days)}d ago`;
  return localDate(then);
}

/* Absolute dates in the user's local timezone, day-month-year. */
function localDate(ms) {
  const d = new Date(ms);
  return `${d.getDate()}-${d.getMonth() + 1}-${d.getFullYear()}`;
}

function localDateTime(ms) {
  return `${localDate(ms)} ${new Date(ms).toLocaleTimeString()}`;
}

function setupDropzone() {
  const zone = $("#dropzone");
  zone.addEventListener("click", () => el.fileInput.click());
  zone.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") el.fileInput.click();
  });
  for (const type of ["dragenter", "dragover"]) {
    zone.addEventListener(type, (e) => {
      e.preventDefault();
      zone.classList.add("drag");
    });
  }
  for (const type of ["dragleave", "drop"]) {
    zone.addEventListener(type, (e) => {
      e.preventDefault();
      zone.classList.remove("drag");
    });
  }
  zone.addEventListener("drop", (e) => {
    const file = e.dataTransfer?.files?.[0];
    if (file) importFile(file);
  });
}

let toastTimer = null;
function toast(message) {
  el.toast.textContent = message;
  el.toast.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    el.toast.hidden = true;
  }, 4000);
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
