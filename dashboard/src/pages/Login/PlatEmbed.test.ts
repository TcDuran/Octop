import { describe, expect, it } from "vitest";
import { isPlatEmbedTicket } from "./PlatEmbed";

describe("embedded ticket handoff", () => {
  it("accepts only the expected host window, origin and OIDC state", () => {
    const parent = {} as Window;
    const payload = {
      type: "plat:ticket",
      state: "challenge",
      ticket: "a".repeat(43),
    };
    const event = (source: unknown, origin: string, data = payload) =>
      ({ source, origin, data }) as MessageEvent;
    expect(
      isPlatEmbedTicket(
        event(parent, "https://host.example"),
        parent,
        "https://host.example",
        "challenge",
      ),
    ).toBe(true);
    expect(
      isPlatEmbedTicket(
        event({}, "https://host.example"),
        parent,
        "https://host.example",
        "challenge",
      ),
    ).toBe(false);
    expect(
      isPlatEmbedTicket(
        event(parent, "https://evil.example"),
        parent,
        "https://host.example",
        "challenge",
      ),
    ).toBe(false);
    expect(
      isPlatEmbedTicket(
        event(parent, "https://host.example"),
        parent,
        "https://host.example",
        "another",
      ),
    ).toBe(false);
    expect(
      isPlatEmbedTicket(
        event(parent, "https://host.example", {
          ...payload,
          ticket: "invalid",
        }),
        parent,
        "https://host.example",
        "challenge",
      ),
    ).toBe(false);
  });
});
