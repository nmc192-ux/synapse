import { SynapseClient, createAgentDefinition } from "../src/index.js";

const agentId = "codex-js-example";
const client = new SynapseClient({
  baseUrl: "http://127.0.0.1:8000",
  agentId
});

await client.registerAgent(
  createAgentDefinition({
    agentId,
    kind: "codex",
    name: "Codex JS Example",
    description: "Example Codex-style agent using the JavaScript SDK.",
    allowedDomains: ["example.com"],
    allowedTools: ["github.search"]
  })
);

const browser = client.browser;
const page = await browser.open("https://example.com");
await browser.click("body");
const repos = await browser.callTool("github.search", {
  query: "browser agents python",
  per_page: 3
});

console.log({
  title: page.page.title,
  repos
});
