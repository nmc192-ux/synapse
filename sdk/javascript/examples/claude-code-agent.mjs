import { SynapseClient, createAgentDefinition } from "../src/index.js";

const agentId = "claude-code-js-example";
const client = new SynapseClient({
  baseUrl: "http://127.0.0.1:8000",
  agentId
});

await client.registerAgent(
  createAgentDefinition({
    agentId,
    kind: "claude_code",
    name: "Claude Code JS Example",
    description: "Example Claude Code-style agent using the JavaScript SDK.",
    allowedDomains: ["example.com"],
    allowedTools: ["echo"]
  })
);

const browser = client.browser;
await browser.open("https://example.com");
const summary = await browser.extract("p");
const echo = await browser.callTool("echo", {
  task: "summarize",
  paragraphs: summary.matches.length
});

console.log({
  paragraphCount: summary.matches.length,
  echo
});
