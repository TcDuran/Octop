import { useEffect, useRef, useState } from "react";
import { Result, Spin } from "antd";
import { useTranslation } from "react-i18next";
import { authApi } from "../../api/modules/auth";
import {
  PLAT_EMBED_SESSION_KEY,
  setRememberLoginPreference,
} from "../../api/request";
import { apiErrorMessage } from "../../utils/apiError";

export const PLAT_EMBED_ORIGIN_KEY = "plat_embed_origin";

/** Accept a ticket only from the exact host window and this authorization request. */
export function isPlatEmbedTicket(
  event: MessageEvent,
  parent: Window,
  origin: string,
  state: string,
): boolean {
  return (
    event.source === parent &&
    event.origin === origin &&
    event.data?.type === "plat:ticket" &&
    event.data.state === state &&
    typeof event.data.ticket === "string" &&
    /^[A-Za-z0-9_-]{43}$/.test(event.data.ticket)
  );
}

export default function PlatEmbed() {
  const { t } = useTranslation();
  const start = useRef<ReturnType<typeof authApi.startPlatEmbed> | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    const origin =
      new URLSearchParams(window.location.search).get("parent_origin") || "";
    let listener: ((event: MessageEvent) => void) | undefined;
    let disposed = false;
    const fail = (err: unknown) => {
      setError(apiErrorMessage(err, t("login.oidcComplete.failed"), t));
    };
    if (window.parent === window) {
      setError(t("login.oidcComplete.failed"));
      return;
    }
    sessionStorage.setItem(PLAT_EMBED_SESSION_KEY, "1");
    // The server checks the exact configured host origin before issuing state.
    start.current ??= authApi.startPlatEmbed(origin);
    void start.current
      .then(({ authorization_url, state }) => {
        if (disposed) return;
        listener = (event: MessageEvent) => {
          if (!isPlatEmbedTicket(event, window.parent, origin, state)) return;
          window.removeEventListener("message", listener!);
          const url = new URL(authorization_url);
          url.searchParams.set("embed_ticket", event.data.ticket);
          sessionStorage.setItem(PLAT_EMBED_ORIGIN_KEY, origin);
          setRememberLoginPreference(false);
          window.location.replace(url.href);
        };
        window.addEventListener("message", listener);
        window.parent.postMessage(
          { type: "plat:authorize", authorization_url, state },
          origin,
        );
      })
      .catch(fail);
    return () => {
      disposed = true;
      if (listener) window.removeEventListener("message", listener);
    };
  }, [t]);
  return error ? (
    <Result
      status="error"
      title={t("login.oidcComplete.title")}
      subTitle={error}
    />
  ) : (
    <div style={{ minHeight: "100dvh", display: "grid", placeItems: "center" }}>
      <Spin size="large" tip={t("login.oidcComplete.loading")} />
    </div>
  );
}
