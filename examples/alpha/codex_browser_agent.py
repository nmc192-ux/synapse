from synapse.models.agent import AgentKind

from common import build_client, restricted_alpha_agent


def main() -> None:
    client = build_client("codex-browser-alpha")
    try:
        client.register_agent(
            restricted_alpha_agent(
                agent_id="codex-browser-alpha",
                kind=AgentKind.CODEX,
                name="Codex Browser Alpha",
                description="Restricted alpha browser workflow example for supervised use.",
                allowed_tools=["github.search"],
                capability_tags=["restricted-alpha", "browser", "supervised"],
            )
        )
        page = client.browser.open("https://example.com")
        repos = client.browser.call_tool("github.search", {"query": "browser agents python", "per_page": 3})
        print(
            {
                "mode": "restricted-alpha",
                "project_id": client.project_id,
                "url": page.page.url,
                "repos": repos,
                "guidance": "Keep domains narrow, keep GitHub usage read-only, and review operator interventions before resuming.",
            }
        )
    finally:
        client.close()


if __name__ == "__main__":
    main()
