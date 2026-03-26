class SynapseHttpError extends Error {
  constructor(message, status, body) {
    super(message);
    this.name = "SynapseHttpError";
    this.status = status;
    this.body = body;
  }
}

async function requestJson(baseUrl, path, options = {}) {
  const response = await fetch(`${baseUrl}${path}`, {
    headers: {
      "content-type": "application/json",
      ...(options.headers || {})
    },
    ...options
  });

  if (!response.ok) {
    let body = null;
    try {
      body = await response.json();
    } catch {
      body = await response.text();
    }
    throw new SynapseHttpError(
      `Synapse request failed: ${response.status} ${response.statusText}`,
      response.status,
      body
    );
  }

  if (response.status === 204) {
    return null;
  }

  return response.json();
}

export class SynapseClient {
  constructor({
    baseUrl = "http://127.0.0.1:8000",
    agentId = null,
    apiKey = null,
    bearerToken = null,
    projectId = null,
    tokenRefresh = null
  } = {}) {
    this.baseUrl = baseUrl.replace(/\/$/, "");
    this.agentId = agentId;
    this.apiKey = apiKey;
    this.bearerToken = bearerToken;
    this.projectId = projectId;
    this.tokenRefresh = tokenRefresh;
    this.browser = new SynapseBrowser(this, { agentId });
  }

  buildHeaders(extraHeaders = {}) {
    const headers = { ...extraHeaders };
    if (this.projectId) {
      headers["X-Synapse-Project-Id"] = this.projectId;
    }
    if (this.bearerToken) {
      headers.Authorization = `Bearer ${this.bearerToken}`;
    } else if (this.apiKey) {
      headers["X-API-Key"] = this.apiKey;
    }
    return headers;
  }

  async request(path, options = {}) {
    try {
      return await requestJson(this.baseUrl, path, {
        ...options,
        headers: this.buildHeaders(options.headers || {})
      });
    } catch (error) {
      if (
        error instanceof SynapseHttpError &&
        error.status === 401 &&
        typeof this.tokenRefresh === "function"
      ) {
        const refreshed = await this.tokenRefresh();
        if (refreshed) {
          this.bearerToken = refreshed;
          return requestJson(this.baseUrl, path, {
            ...options,
            headers: this.buildHeaders(options.headers || {})
          });
        }
      }
      throw error;
    }
  }

  buildWebSocketUrl(path = "/api/ws") {
    const base = this.baseUrl.replace(/^http/, "ws");
    const url = new URL(`${base}${path}`);
    if (this.bearerToken) {
      url.searchParams.set("token", this.bearerToken);
    } else if (this.apiKey) {
      url.searchParams.set("api_key", this.apiKey);
    }
    if (this.projectId) {
      url.searchParams.set("project_id", this.projectId);
    }
    return url.toString();
  }

  async createSession() {
    return this.request("/api/sessions", {
      method: "POST"
    });
  }

  async registerAgent(agent) {
    return this.request("/api/agents", {
      method: "POST",
      body: JSON.stringify(agent)
    });
  }

  async listTools() {
    return this.request("/api/tools", {
      method: "GET"
    });
  }

  async callTool(toolName, params = {}) {
    return this.request("/api/tools/call", {
      method: "POST",
      body: JSON.stringify({
        agent_id: this.agentId,
        tool_name: toolName,
        arguments: params
      })
    });
  }

  async sendAgentMessage({
    senderAgentId,
    recipientAgentId,
    content,
    metadata = {}
  }) {
    return this.request("/api/messages", {
      method: "POST",
      body: JSON.stringify({
        sender_agent_id: senderAgentId,
        recipient_agent_id: recipientAgentId,
        content,
        metadata
      })
    });
  }
}

export class SynapseBrowser {
  constructor(client, { agentId = null, sessionId = null } = {}) {
    this.client = client;
    this.agentId = agentId;
    this._sessionId = sessionId;
  }

  async getSessionId() {
    if (!this._sessionId) {
      const session = await this.client.createSession();
      this._sessionId = session.session_id;
    }
    return this._sessionId;
  }

  async open(url) {
    return this.client.request("/api/browser/open", {
      method: "POST",
      body: JSON.stringify({
        session_id: await this.getSessionId(),
        agent_id: this.agentId,
        url
      })
    });
  }

  async extract(selector, attribute = null) {
    return this.client.request("/api/browser/extract", {
      method: "POST",
      body: JSON.stringify({
        session_id: await this.getSessionId(),
        agent_id: this.agentId,
        selector,
        attribute
      })
    });
  }

  async click(selector) {
    return this.client.request("/api/browser/click", {
      method: "POST",
      body: JSON.stringify({
        session_id: await this.getSessionId(),
        agent_id: this.agentId,
        selector
      })
    });
  }

  async type(selector, text) {
    return this.client.request("/api/browser/type", {
      method: "POST",
      body: JSON.stringify({
        session_id: await this.getSessionId(),
        agent_id: this.agentId,
        selector,
        text
      })
    });
  }

  async screenshot() {
    return this.client.request("/api/browser/screenshot", {
      method: "POST",
      body: JSON.stringify({
        session_id: await this.getSessionId(),
        agent_id: this.agentId
      })
    });
  }

  async callTool(toolName, params = {}) {
    return this.client.callTool(toolName, params);
  }

  async listTools() {
    return this.client.listTools();
  }

  async sendAgentMessage(message) {
    return this.client.sendAgentMessage(message);
  }

  fork({ sessionId = this._sessionId } = {}) {
    return new SynapseBrowser(this.client, {
      agentId: this.agentId,
      sessionId
    });
  }
}

export function createAgentDefinition({
  agentId,
  kind,
  name,
  description = null,
  allowedDomains = [],
  allowedTools = [],
  metadata = {}
}) {
  return {
    agent_id: agentId,
    kind,
    name,
    description,
    security: {
      allowed_domains: allowedDomains,
      allowed_tools: allowedTools,
      rate_limits: {
        browser_actions_per_minute: 30,
        tool_calls_per_minute: 15
      },
      block_unsafe_actions: true
    },
    metadata
  };
}

export { SynapseHttpError };
