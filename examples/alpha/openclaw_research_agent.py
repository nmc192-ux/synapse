from synapse.models.agent import AgentKind

from common import build_client, restricted_alpha_agent


def main() -> None:
    client = build_client("openclaw-research-alpha")
    try:
        client.register_agent(
            restricted_alpha_agent(
                agent_id="openclaw-research-alpha",
                kind=AgentKind.OPENCLAW,
                name="OpenClaw Research Alpha",
                description="Restricted alpha research agent with explicit browsing and approval limits.",
                allowed_tools=["web.search"],
                capability_tags=["restricted-alpha", "research", "supervised"],
            )
        )
        page = client.browser.open("https://example.com")
        heading = client.browser.extract("h1")
        print(
            {
                "mode": "restricted-alpha",
                "project_id": client.project_id,
                "page": page.page.title,
                "heading": heading.model_dump(),
                "note": "Use supervised runs only. Keep domains narrow and do not provide sensitive credentials.",
            }
        )
    finally:
        client.close()


if __name__ == "__main__":
    main()
