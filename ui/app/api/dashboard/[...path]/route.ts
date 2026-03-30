import { NextRequest, NextResponse } from "next/server";

const allowedPaths = new Set(["runs", "agents", "interventions"]);

function sortMergedPayload(path: string[], records: unknown[]): unknown[] {
  if (path[0] === "runs") {
    return [...records].sort((left, right) => {
      const leftValue =
        ((left as Record<string, unknown>).updated_at as string | undefined) ??
        ((left as Record<string, unknown>).completed_at as string | undefined) ??
        ((left as Record<string, unknown>).started_at as string | undefined) ??
        "";
      const rightValue =
        ((right as Record<string, unknown>).updated_at as string | undefined) ??
        ((right as Record<string, unknown>).completed_at as string | undefined) ??
        ((right as Record<string, unknown>).started_at as string | undefined) ??
        "";
      return rightValue.localeCompare(leftValue);
    });
  }
  if (path[0] === "agents") {
    return [...records].sort((left, right) => {
      const leftValue =
        ((left as Record<string, unknown>).last_seen_at as string | undefined) ??
        ((left as Record<string, unknown>).updated_at as string | undefined) ??
        "";
      const rightValue =
        ((right as Record<string, unknown>).last_seen_at as string | undefined) ??
        ((right as Record<string, unknown>).updated_at as string | undefined) ??
        "";
      return rightValue.localeCompare(leftValue);
    });
  }
  return records;
}

function getServerConfig() {
  const baseUrl = process.env.SYNAPSE_SERVER_API_BASE_URL ?? "http://127.0.0.1:8000";
  const projects = [
    {
      alias: "steady",
      apiKey: process.env.SYNAPSE_SERVER_API_KEY ?? "",
      projectId: process.env.SYNAPSE_SERVER_PROJECT_ID ?? "",
    },
    {
      alias: "chaos",
      apiKey: process.env.SYNAPSE_SERVER_CHAOS_API_KEY ?? "",
      projectId: process.env.SYNAPSE_SERVER_CHAOS_PROJECT_ID ?? "",
    },
  ].filter((project) => project.apiKey && project.projectId);
  return { baseUrl, projects };
}

function buildTargetUrl(pathSegments: string[]) {
  const { baseUrl } = getServerConfig();
  const safeBaseUrl = baseUrl.replace(/\/+$/, "");
  const path = pathSegments.join("/");
  return `${safeBaseUrl}/api/${path}`;
}

export async function GET(
  request: NextRequest,
  context: { params: Promise<{ path: string[] }> },
) {
  const { path } = await context.params;
  const nestedRunPath = path[0] === "runs" && path[2] === "worker-requests";
  if (!path.length || (!allowedPaths.has(path[0]) && !nestedRunPath)) {
    return NextResponse.json({ detail: "Not found" }, { status: 404 });
  }

  const { projects } = getServerConfig();
  if (projects.length === 0) {
    return NextResponse.json({ detail: "Dashboard proxy is not configured." }, { status: 503 });
  }

  if (path[0] === "runs" && path.length > 1) {
    for (const project of projects) {
      const targetUrl = new URL(buildTargetUrl(path));
      targetUrl.search = request.nextUrl.search;
      const response = await fetch(targetUrl, {
        method: "GET",
        headers: {
          "X-API-Key": project.apiKey,
          "X-Synapse-Project-Id": project.projectId,
          Accept: "application/json",
        },
        cache: "no-store",
      });
      if (response.status === 404) {
        continue;
      }
      const body = await response.text();
      return new NextResponse(body, {
        status: response.status,
        headers: {
          "content-type": response.headers.get("content-type") ?? "application/json",
          "cache-control": "no-store",
        },
      });
    }
    return NextResponse.json({ detail: "Not found" }, { status: 404 });
  }

  if (path[0] === "runs" || path[0] === "agents" || path[0] === "interventions") {
    const responses = await Promise.all(
      projects.map(async (project) => {
        const targetUrl = new URL(buildTargetUrl(path));
        targetUrl.search = request.nextUrl.search;
        const response = await fetch(targetUrl, {
          method: "GET",
          headers: {
            "X-API-Key": project.apiKey,
            "X-Synapse-Project-Id": project.projectId,
            Accept: "application/json",
          },
          cache: "no-store",
        });
        if (!response.ok) {
          const detail = await response.text();
          throw new Error(`${project.alias}:${response.status}:${detail}`);
        }
        return (await response.json()) as unknown[];
      }),
    );
    const merged = sortMergedPayload(path, responses.flat());
    return NextResponse.json(merged, {
      status: 200,
      headers: {
        "cache-control": "no-store",
      },
    });
  }

  return NextResponse.json({ detail: "Not found" }, { status: 404 });
}

export async function POST() {
  return NextResponse.json({ detail: "Public dashboard is read-only." }, { status: 405 });
}

export async function PUT() {
  return NextResponse.json({ detail: "Public dashboard is read-only." }, { status: 405 });
}

export async function PATCH() {
  return NextResponse.json({ detail: "Public dashboard is read-only." }, { status: 405 });
}

export async function DELETE() {
  return NextResponse.json({ detail: "Public dashboard is read-only." }, { status: 405 });
}
