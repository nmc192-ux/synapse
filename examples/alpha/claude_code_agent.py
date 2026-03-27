from synapse.models.agent import AgentKind

from common import build_client, restricted_alpha_agent


def main() -> None:
    client = build_client("claude-code-alpha")
    try:
        client.register_agent(
            restricted_alpha_agent(
                agent_id="claude-code-alpha",
                kind=AgentKind.CLAUDE_CODE,
                name="Claude Code Alpha",
                description="Restricted alpha extraction and tool example.",
                allowed_tools=["echo"],
                capability_tags=["restricted-alpha", "extraction", "supervised"],
            )
        )
        page = client.browser.open("https://example.com")
        summary = client.browser.extract("p")
        echo = client.browser.call_tool("echo", {"task": "summarize", "page": page.page.title})
        print(
            {
                "mode": "restricted-alpha",
                "project_id": client.project_id,
                "summary": summary.model_dump(),
                "tool": echo,
                "warning": "Do not route sensitive workflows through this example configuration or paste credentials into prompts.",
            }
        )
    finally:
        client.close()


if __name__ == "__main__":
    main()
