import { useEffect, useState } from "react";
import { withBase } from "../lib/paths";

/** Poster/cover `<img>` with no-referrer (CDN hotlink protection) and optional fallback URL on error. */
export function CoverImg({
  src,
  fallback,
  className,
}: {
  src?: string | null;
  fallback?: string | null;
  className?: string;
}) {
  const [url, setUrl] = useState(src || fallback || "");
  useEffect(() => setUrl(src || fallback || ""), [src, fallback]);
  if (!url) return null;
  return (
    <img
      className={className}
      src={withBase(url)}
      alt=""
      decoding="async"
      referrerPolicy="no-referrer"
      onError={() => setUrl((cur) => (fallback && cur !== fallback ? fallback : ""))}
    />
  );
}
