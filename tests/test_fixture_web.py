from fastapi.testclient import TestClient

from synapse.fixtures.web import app


client = TestClient(app)


def test_fixture_index_lists_workflows() -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "Search and Extraction" in response.text
    assert "Login Continuation" in response.text


def test_search_fixture_returns_deterministic_results() -> None:
    response = client.get("/search?q=agents")
    assert response.status_code == 200
    assert "Autonomous Browser Agents" in response.text
    assert 'id="results-count"' in response.text


def test_form_fixture_submission_round_trip() -> None:
    response = client.post(
        "/form/submit",
        data={
            "full_name": "Ava Operator",
            "email": "ava@example.com",
            "workflow": "research",
            "priority": "urgent",
            "notes": "Validate deterministic fixture output.",
            "confirm": "yes",
        },
    )
    assert response.status_code == 200
    assert "Ava Operator" in response.text
    assert "urgent" in response.text
    assert "true" in response.text


def test_download_fixture_serves_attachment() -> None:
    response = client.get("/downloads/report.csv")
    assert response.status_code == 200
    assert response.headers["content-disposition"] == 'attachment; filename="report.csv"'
    assert "fixture benchmark" in response.text


def test_login_fixture_sets_cookie_and_redirects() -> None:
    response = client.post(
        "/auth/login",
        data={"email": "agent@example.com", "password": "synapse"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/account"
    assert "fixture_session=authenticated" in response.headers["set-cookie"]

