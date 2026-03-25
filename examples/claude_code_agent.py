from synapse.models.agent import AgentDefinition, AgentKind, AgentSecurityPolicy
from synapse.sdk import SynapseClient


def main() -> None:
    agent_id = "claude-code-example"
    client = SynapseClient(agent_id=agent_id)
    browser = client.browser

    client.register_agent(
        AgentDefinition(
            agent_id=agent_id,
            kind=AgentKind.CLAUDE_CODE,
            name="Claude Code Example",
            description="Example Claude Code Synapse SDK agent.",
            security=AgentSecurityPolicy(
                allowed_domains=["example.com"],
                allowed_tools=["echo"],
            ),
        )
    )

    page = browser.open("https://example.com")
    summary = browser.extract("p")
    echo = browser.call_tool("echo", {"task": "summarize", "page": page.page.title})
    browser.send_agent_message(
        sender_agent_id="claude-code-example",
        recipient_agent_id="openclaw-example",
        content="Claude Code produced a page summary.",
        metadata={"paragraphs_found": len(summary.matches)},
    )

    print({"summary": summary.model_dump(), "tool": echo})
    client.close()


if __name__ == "__main__":
    main()
