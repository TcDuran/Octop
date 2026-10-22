declare const BASE_URL: string;
import { getBasePath } from "../utils/deploymentPath";

/**
 * Get the full API URL with /api prefix
 * @param path - API path (e.g., "/models", "/skills")
 * @returns Full API URL (e.g., "http://localhost:8088/api/models" or "/api/models")
 */
export function getApiUrl(path: string): string {
  const base =
    (typeof BASE_URL === "undefined" ? "" : BASE_URL) || getBasePath();
  const apiPrefix = "/api";
  const normalizedPath = path.startsWith("/") ? path : `/${path}`;
  return `${base.replace(/\/$/, "")}${apiPrefix}${normalizedPath}`;
}

/**
 * Get a WebSocket URL derived from the same origin as the API.
 * Converts http:// → ws:// and https:// → wss://.
 * If BASE_URL is empty (same-origin), uses the current page location.
 * @param path - API path (e.g., "/browser-stream/ws")
 * @returns Full WebSocket URL
 */
export function getWsUrl(path: string): string {
  const httpUrl = getApiUrl(path);

  if (httpUrl.startsWith("http://")) {
    return httpUrl.replace("http://", "ws://");
  }
  if (httpUrl.startsWith("https://")) {
    return httpUrl.replace("https://", "wss://");
  }

  // Relative URL — derive from current page location
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${window.location.host}${httpUrl}`;
}
