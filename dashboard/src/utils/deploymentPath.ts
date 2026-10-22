/** Public deployment paths are injected by FastAPI before the entry module. */
declare global {
  interface Window {
    __OCTOP_CONFIG__?: { basePath: string };
  }
}

export function getBasePath(): string {
  return window.__OCTOP_CONFIG__?.basePath || "";
}

export function withoutBasePath(path: string): string {
  const base = getBasePath();
  if (base && path === base) return "/";
  return base && path.startsWith(base + "/") ? path.slice(base.length) : path;
}

export function withBasePath(path: string): string {
  if (!path.startsWith("/") || path.startsWith("//")) return path;
  const base = getBasePath();
  if (base && (path === base || path.startsWith(base + "/"))) return path;
  return base + path;
}

/** Keep stored /api links canonical; apply the prefix only at the browser edge. */
export function publicApiUrl(value: string): string {
  let path = value;
  if (/^https?:\/\//i.test(value)) {
    const url = new URL(value);
    if (url.origin !== window.location.origin) return value;
    path = url.pathname + url.search + url.hash;
  }
  const canonical = withoutBasePath(path);
  return canonical.startsWith("/api/") ? withBasePath(canonical) : value;
}

export function apiPathFromUrl(value: string): string {
  const url = /^https?:\/\//i.test(value) ? new URL(value) : null;
  const path = withoutBasePath(url ? url.pathname + url.search : value);
  return path.replace(/^\/api(?=\/|$)/, "");
}
