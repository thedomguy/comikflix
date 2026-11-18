import { useMemo } from "react";
import { useLibrary } from "../lib/library";
import { sortLibrary } from "../lib/filters";
import { SeriesCard } from "../components/SeriesCard";
import "../styles/library.css";

/** #/vault — series marked private, reached only from More → Private. They stay off Home,
 *  Library, Search and Calendar; no password for now. */
export default function VaultPage() {
  const { vault, progressVersion } = useLibrary();
  const shown = useMemo(() => {
    void progressVersion;
    return sortLibrary(vault, "recent");
  }, [vault, progressVersion]);

  return (
    <main className="lib">
      <header className="lib-head">
        <h1>Private</h1>
        <span className="lib-count">
          {shown.length} series
        </span>
      </header>
      {!shown.length ? (
        <div className="lib-empty">
          <p>Nothing here yet. Open a series and use “Move to Private” to keep it here.</p>
        </div>
      ) : (
        <div className="lib-grid">
          {shown.map((s) => (
            <SeriesCard key={s.slug} s={s} />
          ))}
        </div>
      )}
    </main>
  );
}
