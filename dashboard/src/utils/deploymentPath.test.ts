import { afterEach, describe, expect, it, vi } from "vitest";
import { getApiUrl, getWsUrl } from "../api/config";
import { localInviteUrl } from "../api/modules/invites";
import { buildDashboardChatWsUrl } from "../api/modules/wsChat";
import {
  apiPathFromUrl,
  publicApiUrl,
  withBasePath,
  withoutBasePath,
} from "./deploymentPath";
import {
  needsAuthBlobFetch,
  collectToolMediaFromToolData,
} from "./toolMediaBlocks";

afterEach(() => {
  delete window.__OCTOP_CONFIG__;
  vi.unstubAllGlobals();
});

describe.each(["", "/octop", "/apps/octop"])(
  "runtime deployment %s",
  (basePath) => {
    it("builds page/API/WebSocket/invite URLs from the same runtime configuration", () => {
      window.__OCTOP_CONFIG__ = { basePath };
      expect(withBasePath("/login")).toBe(basePath + "/login");
      expect(withoutBasePath(basePath + "/login")).toBe("/login");
      expect(withBasePath(basePath + "/login")).toBe(basePath + "/login");
      expect(getApiUrl("/health")).toBe(basePath + "/api/health");
      expect(getWsUrl("/notifications/ws")).toBe(
        "ws://" + window.location.host + basePath + "/api/notifications/ws",
      );
      expect(new URL(buildDashboardChatWsUrl("agent-1")).pathname).toBe(
        basePath + "/api/agents/agent-1/chat/ws",
      );
      expect(localInviteUrl("a b")).toBe(
        window.location.origin + basePath + "/invite?code=a%20b",
      );
    });

    it("accepts old and prefixed media URLs without doubling the API prefix", () => {
      window.__OCTOP_CONFIG__ = { basePath };
      const canonical =
        "/api/agents/agent/workspace/download?path=inbound%2Ffile.png";
      const publicUrl = basePath + canonical;
      expect(publicApiUrl(canonical)).toBe(publicUrl);
      expect(publicApiUrl(publicUrl)).toBe(publicUrl);
      expect(apiPathFromUrl(publicUrl)).toBe(
        "/agents/agent/workspace/download?path=inbound%2Ffile.png",
      );
      expect(needsAuthBlobFetch(publicUrl)).toBe(true);
      const media = collectToolMediaFromToolData(undefined, "agent", [
        { url: canonical, kind: "image", filename: "file.png" },
        { url: canonical, kind: "file", filename: "file.png" },
      ]);
      expect(media.images[0].url).toBe(publicUrl);
      expect(media.files[0].url).toBe(publicUrl);
      expect(publicApiUrl("https://external.example/api/file")).toBe(
        "https://external.example/api/file",
      );
    });
  },
);

it("does not confuse a neighboring deployment path with the configured prefix", () => {
  window.__OCTOP_CONFIG__ = { basePath: "/octop" };
  expect(withoutBasePath("/octopus/login")).toBe("/octopus/login");
  expect(withBasePath("https://identity.example/login")).toBe(
    "https://identity.example/login",
  );
});

it("preserves the explicit API origin override and derives secure WebSockets", () => {
  window.__OCTOP_CONFIG__ = { basePath: "/octop" };
  vi.stubGlobal("BASE_URL", "https://api.example/remote/");
  expect(getApiUrl("/health")).toBe("https://api.example/remote/api/health");
  expect(getWsUrl("/notifications/ws")).toBe(
    "wss://api.example/remote/api/notifications/ws",
  );
});
