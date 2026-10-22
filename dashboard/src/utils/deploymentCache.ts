import { getBasePath, withBasePath } from "./deploymentPath";

/** A subpath deployment must not unregister another application's worker. */
export async function clearDeploymentCaches(): Promise<void> {
  const prefix = getBasePath();
  const scope = prefix
    ? new URL(withBasePath("/"), window.location.origin).href
    : "";
  if ("serviceWorker" in navigator) {
    const registrations = await navigator.serviceWorker.getRegistrations();
    await Promise.all(
      registrations
        .filter((r) => !prefix || r.scope === scope)
        .map((r) => r.unregister()),
    );
  }
  if (typeof caches === "undefined") return;
  const names = await caches.keys();
  await Promise.all(
    names.map(async (name) => {
      if (!prefix) {
        await caches.delete(name);
        return;
      }
      const cache = await caches.open(name);
      const requests = await cache.keys();
      await Promise.all(
        requests
          .filter((r) => r.url.startsWith(scope))
          .map((r) => cache.delete(r)),
      );
    }),
  );
}
