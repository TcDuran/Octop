import { PLAT_EMBED_SESSION_KEY } from "../api/request";

/** Only the PLAT iframe entry opts into the embedded interface. */
export function isPlatEmbedded(): boolean {
  return (
    window.parent !== window &&
    sessionStorage.getItem(PLAT_EMBED_SESSION_KEY) === "1"
  );
}
