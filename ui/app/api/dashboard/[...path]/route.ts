import { NextRequest, NextResponse } from "next/server";

const allowedPaths = new Set(["runs", "agents", "interventions"]);

function getServerConfig() {
  const baseUrl = process.env.SYNAPSE_SERVER_API_BASE_URL ?? "http://127.0.0.1:8000";
  const apiKey = process.env.SYNAPSE_SERVER_API_KEY ?? "";
  const projectId = process.env.SYNAPSE_SERVER_PROJECT_ID ?? "";
  return { baseUrl, apiKey, projectId };
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
  if (!path.length || !allowedPaths.has(path[0])) {
    return NextResponse.json({ detail: "Not found" }, { status: 404 });
  }

  const { apiKey, projectId } = getServerConfig();
  if (!apiKey || !projectId) {
    return NextResponse.json({ detail: "Dashboard proxy is not configured." }, { status: 503 });
  }

  const targetUrl = new URL(buildTargetUrl(path));
  targetUrl.search = request.nextUrl.search;

  const response = await fetch(targetUrl, {
    method: "GET",
    headers: {
      "X-API-Key": apiKey,
      "X-Synapse-Project-Id": projectId,
      Accept: "application/json",
    },
    cache: "no-store",
  });

  const body = await response.text();
  return new NextResponse(body, {
    status: response.status,
    headers: {
      "content-type": response.headers.get("content-type") ?? "application/json",
      "cache-control": "no-store",
    },
  });
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
