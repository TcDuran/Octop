import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PLAT_EMBED_SESSION_KEY } from "../api/request";
import {
  LAYOUT_MODE_KEY,
  MINIMAL_NAV_PANE_KEY,
} from "../layouts/layoutModeStorage";
import { LayoutModeProvider, useLayoutMode } from "./LayoutModeContext";

function Probe() {
  const mode = useLayoutMode();
  return (
    <>
      <output>{`${mode.isPlatEmbedded}:${mode.layoutMode}:${mode.minimalPane}`}</output>
      <button onClick={() => mode.setLayoutMode("classic")}>classic</button>
      <button onClick={() => mode.setMinimalPane("settings")}>settings</button>
    </>
  );
}

function mount() {
  return render(
    <LayoutModeProvider>
      <Probe />
    </LayoutModeProvider>,
  );
}

describe("PLAT embedded layout", () => {
  afterEach(() => {
    localStorage.removeItem(LAYOUT_MODE_KEY);
    localStorage.removeItem(MINIMAL_NAV_PANE_KEY);
    sessionStorage.removeItem(PLAT_EMBED_SESSION_KEY);
    vi.unstubAllGlobals();
  });

  it("defaults to minimal while standalone users can still switch", () => {
    mount();
    expect(screen.getByRole("status")).toHaveTextContent(
      "false:minimal:records",
    );
    fireEvent.click(screen.getByText("classic"));
    expect(screen.getByRole("status")).toHaveTextContent(
      "false:classic:records",
    );
    expect(localStorage.getItem(LAYOUT_MODE_KEY)).toBe("classic");
  });

  it("requires both the PLAT session marker and an iframe", () => {
    sessionStorage.setItem(PLAT_EMBED_SESSION_KEY, "1");
    localStorage.setItem(LAYOUT_MODE_KEY, "classic");
    const view = mount();
    expect(screen.getByRole("status")).toHaveTextContent(
      "false:classic:records",
    );
    view.unmount();
    sessionStorage.removeItem(PLAT_EMBED_SESSION_KEY);
    vi.stubGlobal("parent", {});
    mount();
    expect(screen.getByRole("status")).toHaveTextContent(
      "false:classic:records",
    );
  });

  it("forces embedded records without changing standalone preferences", () => {
    vi.stubGlobal("parent", {});
    sessionStorage.setItem(PLAT_EMBED_SESSION_KEY, "1");
    localStorage.setItem(LAYOUT_MODE_KEY, "classic");
    localStorage.setItem(MINIMAL_NAV_PANE_KEY, "settings");
    const view = mount();
    expect(screen.getByRole("status")).toHaveTextContent(
      "true:minimal:records",
    );
    fireEvent.click(screen.getByText("classic"));
    fireEvent.click(screen.getByText("settings"));
    expect(screen.getByRole("status")).toHaveTextContent(
      "true:minimal:records",
    );
    expect(localStorage.getItem(LAYOUT_MODE_KEY)).toBe("classic");
    expect(localStorage.getItem(MINIMAL_NAV_PANE_KEY)).toBe("settings");
    view.unmount();
    vi.unstubAllGlobals();
    mount();
    expect(screen.getByRole("status")).toHaveTextContent(
      "false:classic:settings",
    );
  });
});
