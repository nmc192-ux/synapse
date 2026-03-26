export type SynapseUiAuth = {
  bearerToken: string | null;
  apiKey: string | null;
  projectId: string | null;
};

const STORAGE_KEYS = {
  bearerToken: "synapse.bearerToken",
  apiKey: "synapse.apiKey",
  projectId: "synapse.projectId",
} as const;

export function resolveUiAuth(): SynapseUiAuth {
  const fromStorage = typeof window === "undefined"
    ? { bearerToken: null, apiKey: null, projectId: null }
    : {
        bearerToken: window.localStorage.getItem(STORAGE_KEYS.bearerToken),
        apiKey: window.localStorage.getItem(STORAGE_KEYS.apiKey),
        projectId: window.localStorage.getItem(STORAGE_KEYS.projectId),
      };

  return {
    bearerToken: process.env.NEXT_PUBLIC_SYNAPSE_BEARER_TOKEN ?? fromStorage.bearerToken,
    apiKey: process.env.NEXT_PUBLIC_SYNAPSE_API_KEY ?? fromStorage.apiKey,
    projectId: process.env.NEXT_PUBLIC_SYNAPSE_PROJECT_ID ?? fromStorage.projectId,
  };
}

export function buildSynapseHeaders(auth: SynapseUiAuth): HeadersInit {
  const headers: Record<string, string> = {};
  if (auth.bearerToken) {
    headers.Authorization = `Bearer ${auth.bearerToken}`;
  } else if (auth.apiKey) {
    headers["X-API-Key"] = auth.apiKey;
  }
  if (auth.projectId) {
    headers["X-Synapse-Project-Id"] = auth.projectId;
  }
  return headers;
}

export function buildSynapseWebSocketUrl(baseUrl: string, auth: SynapseUiAuth, runId?: string | null): string {
  const url = new URL(baseUrl, typeof window === "undefined" ? "http://127.0.0.1:3000" : window.location.origin);
  if (auth.bearerToken) {
    url.searchParams.set("token", auth.bearerToken);
  } else if (auth.apiKey) {
    url.searchParams.set("api_key", auth.apiKey);
  }
  if (auth.projectId) {
    url.searchParams.set("project_id", auth.projectId);
  }
  if (runId) {
    url.searchParams.set("run_id", runId);
  }
  return url.toString();
}
