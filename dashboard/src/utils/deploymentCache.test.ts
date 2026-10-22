import { afterEach, expect, it, vi } from "vitest";
import { clearDeploymentCaches } from "./deploymentCache";

afterEach(() => {
  delete window.__OCTOP_CONFIG__;
  vi.unstubAllGlobals();
});

it("clears only the current subpath worker and cached requests", async () => {
  window.__OCTOP_CONFIG__ = { basePath: "/apps/octop" };
  const scope = window.location.origin + "/apps/octop/";
  const ownWorker = { scope, unregister: vi.fn().mockResolvedValue(true) };
  const otherWorker = {
    scope: window.location.origin + "/other/",
    unregister: vi.fn(),
  };
  vi.stubGlobal("navigator", {
    serviceWorker: {
      getRegistrations: vi.fn().mockResolvedValue([ownWorker, otherWorker]),
    },
  });
  const ownRequest = { url: scope + "assets/main.js" };
  const otherRequest = {
    url: window.location.origin + "/apps/octopus/assets/main.js",
  };
  const cache = {
    keys: vi.fn().mockResolvedValue([ownRequest, otherRequest]),
    delete: vi.fn().mockResolvedValue(true),
  };
  vi.stubGlobal("caches", {
    keys: vi.fn().mockResolvedValue(["static-chunks"]),
    open: vi.fn().mockResolvedValue(cache),
  });
  await clearDeploymentCaches();
  expect(ownWorker.unregister).toHaveBeenCalledOnce();
  expect(otherWorker.unregister).not.toHaveBeenCalled();
  expect(cache.delete).toHaveBeenCalledExactlyOnceWith(ownRequest);
});
