from __future__ import annotations

from dataclasses import asdict, dataclass

from common import ROLE_SCOPES, build_admin_api, timestamp_slug, write_json_artifact
from synapse.models.platform import APIKeyCreateRequest, OrganizationCreateRequest, ProjectCreateRequest, UserCreateRequest


@dataclass
class BootstrapResult:
    organization_id: str
    steady_project_id: str
    chaos_project_id: str
    issued_keys: dict[str, dict[str, str]]


def _find_by_slug(items: list[object], slug: str) -> object | None:
    for item in items:
        if getattr(item, "slug", None) == slug:
            return item
    return None


def main() -> None:
    organization_slug = "synthetic-alpha-local"
    steady_slug = "synthetic-alpha-steady"
    chaos_slug = "synthetic-alpha-chaos"

    with build_admin_api() as admin:
        organizations = admin.list_organizations()
        organization = _find_by_slug(organizations, organization_slug)
        if organization is None:
            organization = admin.create_organization(
                OrganizationCreateRequest(
                    name="Synthetic Alpha Local",
                    slug=organization_slug,
                    metadata={"created_by": "synthetic_alpha_swarm.bootstrap"},
                )
            )

        projects = admin.list_projects(organization.organization_id)
        steady_project = _find_by_slug(projects, steady_slug)
        if steady_project is None:
            steady_project = admin.create_project(
                ProjectCreateRequest(
                    organization_id=organization.organization_id,
                    name="Synthetic Alpha Steady",
                    slug=steady_slug,
                    description="Baseline synthetic-alpha workload project for local swarm validation.",
                    metadata={"synthetic_alpha": True, "lane": "steady"},
                )
            )

        chaos_project = _find_by_slug(projects, chaos_slug)
        if chaos_project is None:
            chaos_project = admin.create_project(
                ProjectCreateRequest(
                    organization_id=organization.organization_id,
                    name="Synthetic Alpha Chaos",
                    slug=chaos_slug,
                    description="Safe chaos and tenancy isolation project for local swarm validation.",
                    metadata={"synthetic_alpha": True, "lane": "chaos"},
                )
            )

        users = admin.list_users(organization_id=organization.organization_id)
        by_email = {user.email: user for user in users}
        role_users: dict[str, str] = {}
        for role_name in ROLE_SCOPES:
            email = f"{role_name}@synthetic-alpha.local"
            user = by_email.get(email)
            project_ids = [steady_project.project_id]
            if role_name in {"browser-runner-2", "chaos-monkey"}:
                project_ids = [chaos_project.project_id]
            if role_name in {"auditor", "reporter"}:
                project_ids = [steady_project.project_id, chaos_project.project_id]
            if user is None:
                user = admin.create_user(
                    UserCreateRequest(
                        organization_id=organization.organization_id,
                        project_ids=project_ids,
                        email=email,
                        display_name=role_name.replace("-", " ").title(),
                        metadata={"synthetic_alpha_role": role_name},
                    )
                )
            role_users[role_name] = user.user_id

        issued_keys: dict[str, dict[str, str]] = {}
        for role_name, scopes in ROLE_SCOPES.items():
            project_id = steady_project.project_id
            if role_name in {"browser-runner-2", "chaos-monkey"}:
                project_id = chaos_project.project_id
            issued = admin.create_api_key(
                APIKeyCreateRequest(
                    organization_id=organization.organization_id,
                    project_id=project_id,
                    user_id=role_users[role_name],
                    name=f"{role_name}-{timestamp_slug()}",
                    scopes=scopes,
                    metadata={"synthetic_alpha_role": role_name},
                )
            )
            issued_keys[role_name] = {
                "project_id": project_id,
                "api_key": issued.api_key,
                "access_token": issued.access_token,
            }

    result = BootstrapResult(
        organization_id=organization.organization_id,
        steady_project_id=steady_project.project_id,
        chaos_project_id=chaos_project.project_id,
        issued_keys=issued_keys,
    )
    path = write_json_artifact("bootstrap_summary.json", asdict(result))
    print({"artifact": str(path), **asdict(result)})


if __name__ == "__main__":
    main()
