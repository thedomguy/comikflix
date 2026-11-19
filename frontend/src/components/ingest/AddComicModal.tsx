import { useEffect, useState, type FormEvent } from "react";
import { startIngest, watchJob, searchFederated, type FederatedCluster } from "../../lib/ingest";

/** "Add a comic": either paste a slug/URL, or search every enabled provider by title and pick
 *  one. Both end the same way: start_chapter + import, with progress in the floating window
 *  ("Run in background" hands it to History). */
export function AddComicModal({ onClose }: { onClose: () => void }) {
  const [tab, setTab] = useState<"url" | "search">("url");

  useEffect(() => {
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    addEventListener("keydown", onKey);
    return () => {
      document.body.style.overflow = prev;
      removeEventListener("keydown", onKey);
    };
  }, [onClose]);

  return (
    <div className="modal ingest-modal" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="sheet ingest" role="dialog" aria-modal="true" aria-labelledby="ing-title">
        <button className="close" onClick={onClose} aria-label="Close">
          ✕
        </button>
        <h2 id="ing-title">Add a comic</h2>
        <div className="tabs" role="tablist">
          <button role="tab" aria-selected={tab === "url"} className={tab === "url" ? "active" : ""} onClick={() => setTab("url")}>
            Paste a link
          </button>
          <button role="tab" aria-selected={tab === "search"} className={tab === "search" ? "active" : ""} onClick={() => setTab("search")}>
            Search
          </button>
        </div>
        {tab === "url" ? <UrlForm onClose={onClose} /> : <SearchForm onClose={onClose} />}
      </div>
    </div>
  );
}

function UrlForm({ onClose }: { onClose: () => void }) {
  const [series, setSeries] = useState("");
  // Prefilled: only changed when earlier chapters were already read elsewhere.
  const [start, setStart] = useState("1");
  const [upto, setUpto] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    const s = series.trim();
    const first = start.trim() ? parseInt(start, 10) : 1;
    if (!s) return setErr("Enter a series slug or URL.");
    if (!Number.isFinite(first) || first < 1) return setErr("Start chapter must be a whole number ≥ 1.");
    const last = upto ? parseInt(upto, 10) : null;
    if (last != null && (!Number.isFinite(last) || last < first)) return setErr("“Up to” must be at least the start chapter.");
    setBusy(true);
    setErr("");
    try {
      const job = await startIngest({ series: s, start_chapter: first, latest: last });
      watchJob(job.id, "import");
      onClose();
    } catch (e2) {
      setErr(e2 instanceof Error ? e2.message : "Could not start the import");
      setBusy(false);
    }
  }

  return (
    <>
      <p className="hint">
        Reads the series info and indexes chapter page URLs from the start chapter onward (optionally up to a latest
        chapter). Works with Asura Scans, mgeko.cc and MangaDex links. Chapters you already have are skipped.
      </p>
      <form onSubmit={submit} noValidate>
        <div className="full">
          <label htmlFor="ing-series">Series slug or URL</label>
          <input
            id="ing-series"
            placeholder="asurascans.com, mgeko.cc or mangadex.org series URL, or a slug"
            autoComplete="off"
            spellCheck={false}
            autoFocus
            value={series}
            onChange={(e) => setSeries(e.target.value)}
          />
        </div>
        <div>
          <label htmlFor="ing-start">Start chapter</label>
          <input
            id="ing-start"
            type="number"
            inputMode="numeric"
            min={1}
            step={1}
            placeholder="1"
            value={start}
            onChange={(e) => setStart(e.target.value)}
          />
        </div>
        <div>
          <label htmlFor="ing-upto">Up to chapter (optional)</label>
          <input
            id="ing-upto"
            type="number"
            inputMode="numeric"
            min={1}
            placeholder="all"
            value={upto}
            onChange={(e) => setUpto(e.target.value)}
          />
        </div>
        <button className="btn play" type="submit" disabled={busy}>
          {busy ? "Starting…" : "Import"}
        </button>
      </form>
      <div className="err" role="alert">
        {err}
      </div>
    </>
  );
}

/** Search every enabled provider by title, see clustered results with provider coverage, and
 *  import straight from whichever candidate the user picks. */
function SearchForm({ onClose }: { onClose: () => void }) {
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [clusters, setClusters] = useState<FederatedCluster[] | null>(null);
  const [importing, setImporting] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    const q = query.trim();
    if (!q) return setErr("Enter a title to search for.");
    setBusy(true);
    setErr("");
    try {
      const result = await searchFederated(q);
      setClusters(result.results);
      if (!result.results.length) setErr(`No provider returned a match for "${q}".`);
    } catch (e2) {
      setErr(e2 instanceof Error ? e2.message : "Search failed");
    } finally {
      setBusy(false);
    }
  }

  async function importFrom(url: string | null, providerId: string) {
    const series = url || providerId;
    setImporting(series);
    setErr("");
    try {
      const job = await startIngest({ series, start_chapter: 1 });
      watchJob(job.id, "import");
      onClose();
    } catch (e2) {
      setErr(e2 instanceof Error ? e2.message : "Could not start the import");
      setImporting(null);
    }
  }

  return (
    <>
      <p className="hint">Searches Asura Scans, mgeko.cc and MangaDex at once, grouping matches by title (including alternate/Korean titles) so you can pick which source to read from.</p>
      <form onSubmit={submit} noValidate>
        <div className="full">
          <label htmlFor="ing-query">Title</label>
          <input
            id="ing-query"
            placeholder="e.g. Kagurabachi"
            autoComplete="off"
            spellCheck={false}
            autoFocus
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
        <button className="btn play" type="submit" disabled={busy}>
          {busy ? "Searching…" : "Search"}
        </button>
      </form>
      <div className="err" role="alert">
        {err}
      </div>
      {clusters?.map((cluster) => (
        <div className="federated-cluster" key={cluster.title}>
          <h3>{cluster.title}</h3>
          {cluster.candidates.map((c) => (
            <div className="federated-candidate" key={`${c.provider}:${c.provider_id}`}>
              <span className="provider-badge">{c.provider}</span>
              <span className="candidate-title">{c.title}</span>
              <span className="candidate-chapters">
                {c.chapter_count != null ? `${c.chapter_count} ch.` : c.coverage_error ? "no chapters available" : "coverage unknown"}
              </span>
              <button
                className="btn"
                disabled={!c.can_read_pages || importing === (c.url || c.provider_id)}
                onClick={() => importFrom(c.url, c.provider_id)}
              >
                {importing === (c.url || c.provider_id) ? "Starting…" : "Import"}
              </button>
            </div>
          ))}
        </div>
      ))}
    </>
  );
}
