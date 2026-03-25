from synapse.models.agent import AgentDefinition, AgentKind
from synapse.sdk import SynapseClient


def main() -> None:
    client = SynapseClient()
    browser = client.browser

    client.register_agent(
        AgentDefinition(
            agent_id="codex-example",
            kind=AgentKind.CODEX,
            name="Codex Example",
            description="Example Codex Synapse SDK agent.",
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
