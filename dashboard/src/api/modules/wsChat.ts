import { getAuthToken } from "../request";
import { getWsUrl } from "../config";

export function buildDashboardChatWsUrl(agentId: string): string {
  const token = getAuthToken();
  const encoded = encodeURIComponent(agentId);
  const base = getWsUrl(`/agents/${encoded}/chat/ws`);
  const params = new URLSearchParams();
  if (token) params.set("token", token);
  return params.toString() ? `${base}?${params.toString()}` : base;
}
