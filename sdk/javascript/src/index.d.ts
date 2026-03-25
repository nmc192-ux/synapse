export interface SynapseClientOptions {
  baseUrl?: string;
  agentId?: string | null;
}

export interface AgentDefinitionInput {
  agentId: string;
  kind: string;
  name: string;
  description?: string | null;
  allowedDomains?: string[];
  allowedTools?: string[];
  metadata?: Record<string, string>;
}

export declare class SynapseHttpError extends Error {
  status: number;
  body: unknown;
  constructor(message: string, status: number, body: unknown);
}

export declare class SynapseClient {
  baseUrl: string;
  agentId: string | null;
  browser: SynapseBrowser;
  constructor(options?: SynapseClientOptions);
  createSession(): Promise<any>;
  registerAgent(agent: Record<string, unknown>): Promise<any>;
  listTools(): Promise<any[]>;
  callTool(toolName: string, params?: Record<string, unknown>): Promise<any>;
  sendAgentMessage(message: {
    senderAgentId: string;
    recipientAgentId: string;
    content: string;
    metadata?: Record<string, unknown>;
  }): Promise<any>;
}

export declare class SynapseBrowser {
  agentId: string | null;
  constructor(client: SynapseClient, options?: { agentId?: string | null; sessionId?: string | null });
  getSessionId(): Promise<string>;
  open(url: string): Promise<any>;
  extract(selector: string, attribute?: string | null): Promise<any>;
  click(selector: string): Promise<any>;
  type(selector: string, text: string): Promise<any>;
  screenshot(): Promise<any>;
  callTool(toolName: string, params?: Record<string, unknown>): Promise<any>;
  listTools(): Promise<any[]>;
  sendAgentMessage(message: {
    senderAgentId: string;
    recipientAgentId: string;
    content: string;
    metadata?: Record<string, unknown>;
  }): Promise<any>;
  fork(options?: { sessionId?: string | null }): SynapseBrowser;
}

export declare function createAgentDefinition(input: AgentDefinitionInput): Record<string, unknown>;
