/** Settings + reading progress: in-memory cache backed by API (not localStorage). */

import { withBase } from "./paths";

const KEY = "comicflix";
const LEGACY = {
  width: "comicflix-width",
  sort: "comicflix-sort",
  dismissed: "comicflix-dismissed",
};

const defaultAutoScroll = () => ({
  speed: 40, // px/sec
  persist: "global", // global | series | chapter
  bySeries: {},
  byChapter: {},
});

const defaults = () => ({
  progress: {},
  readerWidth: 800,
  sortNewest: true,
  autoScroll: defaultAutoScroll(),
});

let state = defaults();
let booted = false;
let bootPromise = null;

// Progress is kept in memory on every scroll pause, but only written to the server at a
// steady pace: at most one write per PROGRESS_EVERY_MS while reading, the rest coalesced
// into it. Leaving the chapter, hiding the tab or closing it flushes right away, so the
// spot is never older than this on another device.
const pendingProgress = new Map();
const lastSent = new Map(); // slug -> what the server has, to skip writes that change nothing
let progressTimer = null;
let lastProgressWrite = 0;
const PROGRESS_EVERY_MS = 15000;
const PROGRESS_SETTLE_MS = 1500; // first write after a quiet spell waits for scrolling to pause
const REFRESH_EVERY_MS = 30000; // pulling progress on focus/visible: at most this often
let lastRefresh = 0;
let settingsTimer = null;
const SETTINGS_DEBOUNCE_MS = 600; // sliders fire on every step

async function fetchJson(url) {
  try {
    const res = await fetch(url);
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  }
}

async function putJson(url, body) {
  try {
    const res = await fetch(url, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      keepalive: true, // still delivered when the write is the tab closing
    });
    if (!res.ok) return false;
    return (await res.json().catch(() => null)) || true;
  } catch {
    /* offline / API missing — keep memory cache */
    return false;
  }
}

/** Chapters read in either list (the server never loses one the app sent). */
function unionRead(a, b) {
  const seen = new Set(a || []);
  return [...(a || []), ...(b || []).filter((c) => !seen.has(c))];
}

function normalizeAutoScroll(raw) {
  const out = defaultAutoScroll();
  if (!raw || typeof raw !== "object") return out;
  const speed = +raw.speed;
  if (Number.isFinite(speed)) out.speed = Math.min(1000, Math.max(10, Math.round(speed)));
  const persist = String(raw.persist || out.persist).toLowerCase();
  if (persist === "global" || persist === "series" || persist === "chapter") out.persist = persist;
  for (const [srcKey, destKey] of [
    ["bySeries", "bySeries"],
    ["byChapter", "byChapter"],
  ]) {
    const src = raw[srcKey];
    if (!src || typeof src !== "object") continue;
    const cleaned = {};
    for (const [k, v] of Object.entries(src)) {
      const n = +v;
      if (Number.isFinite(n)) cleaned[String(k)] = Math.min(1000, Math.max(10, Math.round(n)));
    }
    out[destKey] = cleaned;
  }
  return out;
}

function settingsPayload() {
  return {
    readerWidth: state.readerWidth,
    sortNewest: state.sortNewest,
    autoScroll: state.autoScroll,
  };
}

function putSettings() {
  clearTimeout(settingsTimer);
  settingsTimer = null;
  return putJson(withBase("/api/settings"), settingsPayload());
}

/** Settings changed from a slider or picker: one write once it stops moving. */
function scheduleSettings() {
  clearTimeout(settingsTimer);
  settingsTimer = setTimeout(putSettings, SETTINGS_DEBOUNCE_MS);
}

const sameAsSent = (slug, d) => {
  const s = lastSent.get(slug);
  return !!s && s.chapter === d.chapter && Math.abs((s.frac || 0) - (d.frac || 0)) < 0.002 && s.readKey === (d.read || []).join(",");
};
const rememberSent = (slug, d) => lastSent.set(slug, { chapter: d.chapter, frac: d.frac, readKey: (d.read || []).join(",") });

function putProgressSlug(slug, data) {
  return putJson(withBase(`/api/progress/${encodeURIComponent(slug)}`), {
    chapter: data.chapter,
    frac: data.frac,
    read: data.read || [],
    at: data.at,
  });
}

function scheduleProgressWrite(slug, { now = false } = {}) {
  const data = state.progress[slug];
  if (!data) return;
  if (sameAsSent(slug, data)) {
    pendingProgress.delete(slug);
    return;
  }
  pendingProgress.set(slug, data);
  if (now) return void flushProgress();
  if (progressTimer) return; // a write is already due; it will carry this one
  const wait = Math.max(PROGRESS_SETTLE_MS, lastProgressWrite + PROGRESS_EVERY_MS - Date.now());
  progressTimer = setTimeout(flushProgress, wait);
}

async function flushProgress() {
  clearTimeout(progressTimer);
  progressTimer = null;
  const entries = [...pendingProgress.entries()].filter(([slug, data]) => !sameAsSent(slug, data));
  pendingProgress.clear();
  if (!entries.length) return;
  lastProgressWrite = Date.now();
  await Promise.all(
    entries.map(async ([slug, data]) => {
      rememberSent(slug, data);
      const saved = await putProgressSlug(slug, data);
      if (!saved) {
        lastSent.delete(slug); // didn't land: let the next save retry it
        if (!pendingProgress.has(slug)) scheduleProgressWrite(slug);
        return;
      }
      // The server merges in chapters read elsewhere; take them unless a newer write is queued.
      const cur = state.progress[slug];
      if (saved && Array.isArray(saved.read) && cur && !pendingProgress.has(slug)) {
        cur.read = unionRead(cur.read, saved.read);
        window.dispatchEvent(new Event("comikflix:progress"));
      }
    })
  );
}

/** Read and normalize any pre-API localStorage blob (does not write back). */
function readLocalMigration() {
  let raw = null;
  try {
    raw = JSON.parse(localStorage.getItem(KEY) || "null");
  } catch {
    raw = null;
  }

  const hasKey = (() => {
    try {
      return localStorage.getItem(KEY) != null;
    } catch {
      return false;
    }
  })();

  let legacyHit = false;
  const next = defaults();

  if (raw && raw.v === 1 && raw.progress) {
    Object.assign(next, {
      progress: raw.progress || {},
      readerWidth: raw.readerWidth ?? next.readerWidth,
      sortNewest: raw.sortNewest ?? next.sortNewest,
    });
  } else if (raw && !raw.v && typeof raw === "object") {
    const looksLikeProgress = Object.values(raw).some(
      (v) => v && typeof v === "object" && ("chapter" in v || "read" in v)
    );
    if (looksLikeProgress) next.progress = raw;
    else if (raw.progress) Object.assign(next, raw);
  } else if (raw?.progress) {
    Object.assign(next, raw);
  }

  try {
    const w = +(localStorage.getItem(LEGACY.width) || "");
    if (Number.isFinite(w) && w > 0) {
      next.readerWidth = Math.min(1400, Math.max(400, w));
      legacyHit = true;
    }
  } catch {}
  try {
    const s = localStorage.getItem(LEGACY.sort) || "";
    if (s === "oldest" || s.endsWith("-asc")) {
      next.sortNewest = false;
      legacyHit = true;
    } else if (s) {
      next.sortNewest = true;
      legacyHit = true;
    }
  } catch {}

  const hadLocal = hasKey || legacyHit || Object.keys(next.progress).length > 0;
  return hadLocal ? next : null;
}

function clearLocalStorage() {
  try {
    localStorage.removeItem(KEY);
    localStorage.removeItem(LEGACY.width);
    localStorage.removeItem(LEGACY.sort);
    localStorage.removeItem(LEGACY.dismissed);
  } catch {}
}

function applySettings(src) {
  if (!src || typeof src !== "object") return;
  if (src.readerWidth != null) {
    const w = +src.readerWidth;
    if (Number.isFinite(w) && w > 0) state.readerWidth = Math.min(1400, Math.max(400, w));
  }
  if (src.sortNewest != null) state.sortNewest = !!src.sortNewest;
  if (src.autoScroll != null) state.autoScroll = normalizeAutoScroll(src.autoScroll);
}

function applyProgressMap(map) {
  if (!map || typeof map !== "object") return;
  // Accept either a bare slug→entry map or { progress: { ... } }
  const src = map.progress && typeof map.progress === "object" && !("chapter" in map.progress)
    ? map.progress
    : map;
  for (const [slug, entry] of Object.entries(src)) {
    if (!entry || typeof entry !== "object") continue;
    if (!("chapter" in entry || "read" in entry || "frac" in entry)) continue;
    state.progress[slug] = {
      chapter: entry.chapter,
      frac: entry.frac ?? 0,
      read: Array.isArray(entry.read) ? entry.read : [],
      at: entry.at || Date.now(),
      readAt: entry.read_at ?? entry.readAt ?? null, // last read in the app (null: only imported)
    };
  }
}

async function doBootstrap() {
  const local = readLocalMigration();
  const [apiSettings, apiProgress] = await Promise.all([
    fetchJson(withBase("/api/settings")),
    fetchJson(withBase("/api/progress")),
  ]);

  state = defaults();
  applySettings(apiSettings);
  applyProgressMap(apiProgress);
  for (const [slug, d] of Object.entries(state.progress)) rememberSent(slug, d);
  lastRefresh = Date.now();

  if (local) {
    // One-time migrate: browser cache wins, then push to API and clear localStorage.
    // Only clear localStorage after a successful push so a missing API does not wipe data.
    applySettings(local);
    state.progress = { ...state.progress, ...local.progress };
    const settingsOk = await putSettings();
    const progressSlugs = Object.keys(state.progress);
    const progressOk = (
      await Promise.all(progressSlugs.map((slug) => putProgressSlug(slug, state.progress[slug])))
    ).every(Boolean);
    if (settingsOk && (progressSlugs.length === 0 || progressOk)) {
      clearLocalStorage();
    }
  }

  booted = true;
  return state;
}

/** Pull progress changed elsewhere (another device, Jarvis) into this tab: the newer
 *  timestamp wins; series with an unsent local write are left alone. */
async function refreshProgress() {
  if (!booted) return;
  lastRefresh = Date.now();
  const map = await fetchJson(withBase("/api/progress"));
  if (!map || typeof map !== "object") return;
  let changed = false;
  for (const [slug, e] of Object.entries(map)) {
    if (!e || typeof e !== "object" || pendingProgress.has(slug)) continue;
    const cur = state.progress[slug];
    if (cur && (e.at || 0) <= (cur.at || 0)) {
      const read = unionRead(cur.read, Array.isArray(e.read) ? e.read : []);
      if (read.length !== (cur.read || []).length) {
        cur.read = read;
        changed = true;
      }
      continue;
    }
    state.progress[slug] = {
      chapter: e.chapter,
      frac: e.frac ?? 0,
      read: Array.isArray(e.read) ? e.read : [],
      at: e.at || Date.now(),
      readAt: e.read_at ?? null,
    };
    rememberSent(slug, state.progress[slug]);
    changed = true;
  }
  if (changed) window.dispatchEvent(new Event("comikflix:progress"));
}

function flushAll() {
  flushProgress();
  if (settingsTimer) putSettings();
}

function ensureFlushHooks() {
  if (ensureFlushHooks.done) return;
  ensureFlushHooks.done = true;
  window.addEventListener("pagehide", flushAll);
  // Coming back to the app (tab shown, window focused) pulls progress from other devices,
  // but not more than every REFRESH_EVERY_MS: on a laptop, focus fires on every click back
  // into the window (devtools, another app), and visible + focus often fire together.
  const pull = () => {
    if (Date.now() - lastRefresh < REFRESH_EVERY_MS) return;
    refreshProgress();
  };
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") flushAll();
    else pull();
  });
  window.addEventListener("focus", pull);
}

export const store = {
  /** Load settings + progress from API (and migrate localStorage once). Await before routing. */
  bootstrap() {
    if (!bootPromise) {
      ensureFlushHooks();
      bootPromise = doBootstrap();
    }
    return bootPromise;
  },
  get ready() {
    return booted;
  },
  /** Pull progress changed on the server (other devices, Jarvis, a series moved to a new slug). */
  refreshProgress,
  get() {
    return state;
  },
  progress(slug) {
    return state.progress[slug] || null;
  },
  saveProgress(slug, chapter, frac, done) {
    const cur = state.progress[slug] || { read: [] };
    const read = new Set(cur.read || []);
    const finished = done && !read.has(chapter);
    if (done) read.add(chapter);
    const now = Date.now();
    state.progress[slug] = { chapter, frac, read: [...read], at: now, readAt: now };
    // Finishing a chapter goes out at once; scroll positions ride the steady pace.
    scheduleProgressWrite(slug, { now: finished });
    window.dispatchEvent(new Event("comikflix:progress"));
  },
  flush() {
    return flushProgress();
  },
  get readerWidth() {
    return state.readerWidth;
  },
  setReaderWidth(n) {
    state.readerWidth = Math.min(1400, Math.max(400, +n || 800));
    scheduleSettings();
    return state.readerWidth;
  },
  get sortNewest() {
    return state.sortNewest;
  },
  setSortNewest(v) {
    state.sortNewest = !!v;
    putSettings();
  },
  get autoScroll() {
    return state.autoScroll;
  },
  /** Effective speed for a series/chapter, respecting persist scope. */
  autoScrollSpeed(slug, chapterId) {
    const a = state.autoScroll || defaultAutoScroll();
    if (a.persist === "chapter") {
      const key = `${slug}:${chapterId}`;
      if (a.byChapter[key] != null) return a.byChapter[key];
    }
    if (a.persist === "series" || a.persist === "chapter") {
      if (a.bySeries[slug] != null) return a.bySeries[slug];
    }
    return a.speed;
  },
  /**
   * Update speed + persist preference.
   * Writes into the active scope bucket so the next open of that chapter/series
   * (or globally) picks it up.
   */
  setAutoScroll({ speed, persist, slug, chapterId } = {}) {
    const a = normalizeAutoScroll(state.autoScroll);
    if (persist === "global" || persist === "series" || persist === "chapter") {
      a.persist = persist;
    }
    const n = speed != null ? Math.min(1000, Math.max(10, Math.round(+speed))) : null;
    if (n != null) {
      if (a.persist === "chapter" && slug && chapterId != null) {
        a.byChapter[`${slug}:${chapterId}`] = n;
      } else if (a.persist === "series" && slug) {
        a.bySeries[slug] = n;
      } else {
        a.speed = n;
      }
    }
    state.autoScroll = a;
    scheduleSettings();
    return a;
  },
};
