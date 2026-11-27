import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, useSyncExternalStore, type ReactNode } from "react";
import { api } from "./api";
import { thumb } from "./format";
import { withBase } from "./paths";
import { store } from "./store";
import type { Series } from "./types";

interface LibraryState {
  /** Everything outside the private vault (what Home, Library, Search, Calendar show). */
  library: Series[];
  /** Series marked private: only the vault page (More → Private) lists them. */
  vault: Series[];
  ready: boolean;
  error: string | null;
  /** Re-fetch /api/library (after an ingest, a release-date edit, ...). */
  refresh: () => Promise<void>;
  bySlug: (slug: string | undefined) => Series | undefined;
}

const Ctx = createContext<LibraryState | null>(null);

function fixLibrary(list: Series[]): Series[] {
  return list.map((s) => {
    // Parse chapter dates once here; series.ts (unread/new counts, sorting, calendar) reads `ts`.
    // The list is fresh JSON, so filling the field in place is safe.
    for (const c of s.chapters) c.ts = c.date ? Date.parse(c.date) || 0 : 0;
    return {
      ...s,
      // Covers go through the CDN resizer (cards ~190px wide, hero/banner full width).
      poster: thumb(withBase(s.poster), 400) ?? null,
      backdrop: thumb(withBase(s.backdrop), 1280) ?? null,
    };
  });
}

const fetchLibrary = async () => fixLibrary(await api<Series[]>("/api/library"));

/** Library request started at boot (main.tsx), consumed by LibraryProvider's first refresh(). */
let early: Promise<Series[] | null> | null = null;

/** Start /api/library and the settings/progress bootstrap right away, in parallel with App's
 *  /api/me check, instead of after it. A 401 still opens the login (api() fires LOGIN_EVENT). */
export function preloadLibrary() {
  if (early) return;
  early = fetchLibrary().catch(() => null); // failed: the provider fetches again (and reports the error)
  void store.bootstrap();
}

export function LibraryProvider({ children }: { children: ReactNode }) {
  const [all, setAll] = useState<Series[]>([]);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const ref = useRef<Series[]>([]);

  const refresh = useCallback(async () => {
    try {
      const pre = early;
      early = null; // only the first refresh may use the boot request; later ones refetch
      // Progress comes along: when a sync moves a series to a new slug, the server moves its
      // progress too, and without it the series would look never started (and leave Home's rows).
      const [data] = await Promise.all([(pre && (await pre)) || fetchLibrary(), store.refreshProgress()]);
      ref.current = data;
      setAll(data);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load the library");
    }
  }, []);

  useEffect(() => {
    // Settings + progress must be ready before any page reads the store.
    Promise.all([store.bootstrap(), refresh()]).finally(() => setReady(true));
  }, [refresh]);

  // Lookups by slug cover the vault too, so its series and reader pages open normally.
  const bySlug = useCallback((slug: string | undefined) => ref.current.find((s) => s.slug === slug), []);
  const library = useMemo(() => all.filter((s) => !s.private), [all]);
  const vault = useMemo(() => all.filter((s) => s.private), [all]);

  const value = useMemo(
    () => ({ library, vault, ready, error, refresh, bySlug }),
    [library, vault, ready, error, refresh, bySlug],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

/** Bumps whenever reading progress changes (saved here, or pulled from another device).
 *  Read it in a component that shows progress so it re-renders on it. Kept apart from the
 *  library context so a progress save re-renders only those components, not the whole app. */
export function useProgressVersion(): number {
  return useSyncExternalStore(store.subscribeProgress, () => store.progressVersion);
}

export function useLibrary() {
  const v = useContext(Ctx);
  if (!v) throw new Error("useLibrary outside LibraryProvider");
  return v;
}

/** Current library for non-React code (screen.js). */
export let libraryRef: { current: Series[] } = { current: [] };
export function LibraryRefSync() {
  const { library, vault } = useLibrary();
  // Remote screens look series up by slug, so vault series must be findable here too.
  libraryRef.current = vault.length ? [...library, ...vault] : library;
  return null;
}
