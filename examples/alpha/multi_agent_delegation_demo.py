from synapse.models.agent import AgentKind

from common import build_client, restricted_alpha_agent


def main() -> None:
    client = build_client("alpha-coordinator")
    try:
        coordinator = restricted_alpha_agent(
            agent_id="alpha-coordinator",
            kind=AgentKind.CUSTOM,
            name="Alpha Coordinator",
            description="Coordinates supervised browsing tasks in restricted alpha.",
            allowed_tools=["web.search"],
            capability_tags=["restricted-alpha", "coordinator", "supervised"],
        )
        researcher = restricted_alpha_agent(
            agent_id="alpha-researcher",
            kind=AgentKind.OPENCLAW,
            name="Alpha Researcher",
            description="Secondary research agent for restricted alpha demos.",
            allowed_tools=["web.search"],
            capability_tags=["restricted-alpha", "research", "delegation", "supervised"],
        )
        client.register_agent(coordinator)
        client.register_agent(researcher)
        page = client.browser.open("https://example.com")
        client.browser.send_agent_message(
            sender_agent_id="alpha-coordinator",
            recipient_agent_id="alpha-researcher",
            content="Review the landing page and wait for supervised approval before expanding scope.",
            metadata={"page_title": page.page.title, "restricted_alpha": True},
        )
        print(
            {
                "mode": "restricted-alpha",
                "project_id": client.project_id,
                "message": "Delegation demo sent",
                "page_title": page.page.title,
                "note": "This demo shows same-project coordination only. Keep partner workflows supervised and narrowly scoped.",
            }
        )
    finally:
        client.close()


if __name__ == "__main__":
    main()
