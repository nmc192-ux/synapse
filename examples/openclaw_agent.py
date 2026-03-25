from synapse.models.agent import AgentDefinition, AgentKind, AgentSecurityPolicy
from synapse.sdk import SynapseClient


def main() -> None:
    agent_id = "openclaw-example"
    client = SynapseClient(agent_id=agent_id)
    browser = client.browser

    client.register_agent(
        AgentDefinition(
            agent_id=agent_id,
            kind=AgentKind.OPENCLAW,
            name="OpenClaw Example",
            description="Example OpenClaw-style Synapse SDK agent.",
            security=AgentSecurityPolicy(
                allowed_domains=["example.com"],
                allowed_tools=["web.search"],
            ),
        )
    )

    page = browser.open("https://example.com")
    extracted = browser.extract("h1")
    tool_result = browser.call_tool("web.search", {"query": "Synapse browser runtime"})
    browser.send_agent_message(
        sender_agent_id="openclaw-example",
        recipient_agent_id="codex-example",
        content="OpenClaw finished initial discovery.",
        metadata={"page_title": page.page.title},
    )

    print({"title": page.page.title, "extract": extracted.model_dump(), "tool": tool_result})
    client.close()


if __name__ == "__main__":
    main()
