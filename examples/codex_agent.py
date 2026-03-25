from synapse.models.agent import AgentDefinition, AgentKind, AgentSecurityPolicy
from synapse.sdk import SynapseClient


def main() -> None:
    agent_id = "codex-example"
    client = SynapseClient(agent_id=agent_id)
    browser = client.browser

    client.register_agent(
        AgentDefinition(
            agent_id=agent_id,
            kind=AgentKind.CODEX,
            name="Codex Example",
            description="Example Codex Synapse SDK agent.",
            security=AgentSecurityPolicy(
                allowed_domains=["example.com"],
                allowed_tools=["github.search"],
            ),
        )
    )

    page = browser.open("https://example.com")
    heading = browser.extract("h1")
    repo_search = browser.call_tool("github.search", {"query": "browser agents python", "per_page": 3})
    browser.send_agent_message(
        sender_agent_id="codex-example",
        recipient_agent_id="claude-code-example",
        content="Codex inspected the landing page and GitHub ecosystem.",
        metadata={"links": page.page.links[:3]},
    )

    print({"heading": heading.model_dump(), "repos": repo_search})
    client.close()


if __name__ == "__main__":
    main()
