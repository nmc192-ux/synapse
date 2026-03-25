from synapse.models.agent import AgentDefinition, AgentKind
from synapse.sdk import SynapseClient


def main() -> None:
    client = SynapseClient()
    browser = client.browser

    client.register_agent(
        AgentDefinition(
            agent_id="claude-code-example",
            kind=AgentKind.CLAUDE_CODE,
            name="Claude Code Example",
            description="Example Claude Code Synapse SDK agent.",
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
