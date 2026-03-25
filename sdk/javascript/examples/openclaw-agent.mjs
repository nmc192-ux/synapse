import { SynapseClient, createAgentDefinition } from "../src/index.js";

const agentId = "openclaw-js-example";
const client = new SynapseClient({
  baseUrl: "http://127.0.0.1:8000",
  agentId
});

await client.registerAgent(
  createAgentDefinition({
    agentId,
    kind: "openclaw",
    name: "OpenClaw JS Example",
    description: "Example OpenClaw-style agent using the JavaScript SDK.",
    allowedDomains: ["example.com"],
    allowedTools: ["web.search"]
  })
);

const browser = client.browser;
const page = await browser.open("https://example.com");
const heading = await browser.extract("h1");
const toolResult = await browser.callTool("web.search", {
  query: "Synapse browser runtime"
});

console.log({
  title: page.page.title,
  headingCount: heading.matches.length,
  toolResult
});
