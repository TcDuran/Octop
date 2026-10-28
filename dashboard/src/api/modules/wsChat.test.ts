import { describe, expect, it, vi } from "vitest";
import { getAuthToken } from "../request";
import { getWsUrl } from "../config";
import { buildDashboardChatWsUrl } from "./wsChat";

vi.mock("../config", () => ({
  getWsUrl: vi.fn((path: string) => `/octop/api${path}`),
}));

vi.mock("../request", () => ({
  getAuthToken: vi.fn(() => "jwt"),
}));

describe("buildDashboardChatWsUrl", () => {
  it("uses the configured public base URL", () => {
    expect(buildDashboardChatWsUrl("4Q9V0P")).toBe(
      "/octop/api/agents/4Q9V0P/chat/ws?token=jwt",
    );
    expect(getWsUrl).toHaveBeenCalledWith("/agents/4Q9V0P/chat/ws");
    expect(getAuthToken).toHaveBeenCalled();
  });
});
