from __future__ import annotations

import json
from datetime import datetime, timezone
from urllib.parse import parse_qs

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response


app = FastAPI(title="Synapse Fixture Web")

SEARCH_FIXTURES = [
    {
        "title": "Autonomous Browser Agents with Structured Page Models",
        "authors": "A. Chen, P. Malik",
        "summary": "A benchmark paper focused on action-oriented web extraction.",
        "tags": ["agents", "browser", "spm"],
    },
    {
        "title": "Reproducible Web Benchmarks for Runtime Evaluation",
        "authors": "L. Ortiz, R. Ahmed",
        "summary": "Techniques for deterministic browser workflows under automation.",
        "tags": ["benchmark", "testing", "runtime"],
    },
    {
        "title": "Multi-Agent Task Delegation on the Open Web",
        "authors": "N. Das, E. Freeman",
        "summary": "Task decomposition and handoff strategies across web-native agents.",
        "tags": ["agents", "delegation", "workflow"],
    },
]

LAZY_ITEMS = [
    {"title": f"Feed Item {index}", "excerpt": f"Deterministic lazy-loaded excerpt {index}."}
    for index in range(1, 31)
]

SPA_ROUTES = {
    "overview": {
        "title": "Overview",
        "body": "Runtime overview with current status indicators and a summary card.",
    },
    "search": {
        "title": "Search",
        "body": "Search tab with filter controls and result summaries for extraction.",
    },
    "settings": {
        "title": "Settings",
        "body": "Settings tab with toggles, profile selection, and save controls.",
    },
}


def render_page(title: str, body: str, *, script: str = "", extra_head: str = "") -> HTMLResponse:
    html = f"""
    <!doctype html>
    <html lang="en">
      <head>
        <meta charset="utf-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <title>{title}</title>
        <style>
          :root {{
            color-scheme: light;
            --bg: #f4f1e8;
            --panel: #fffaf2;
            --ink: #14213d;
            --muted: #5f6b7a;
            --accent: #bc6c25;
            --line: #d9c9ad;
            --warn: #a61e4d;
          }}
          * {{ box-sizing: border-box; }}
          body {{
            margin: 0;
            font-family: Georgia, "Times New Roman", serif;
            background:
              radial-gradient(circle at top left, rgba(188,108,37,0.16), transparent 22rem),
              linear-gradient(180deg, #f8f4ec 0%, var(--bg) 100%);
            color: var(--ink);
          }}
          a {{ color: var(--accent); }}
          .shell {{
            width: min(1100px, calc(100vw - 2rem));
            margin: 0 auto;
            padding: 2rem 0 4rem;
          }}
          .masthead {{
            display: flex;
            justify-content: space-between;
            gap: 1rem;
            align-items: start;
            margin-bottom: 1.5rem;
          }}
          .masthead h1 {{
            margin: 0;
            font-size: clamp(1.8rem, 4vw, 3rem);
            letter-spacing: -0.04em;
          }}
          .masthead p {{
            margin: 0.35rem 0 0;
            color: var(--muted);
            max-width: 45rem;
            line-height: 1.5;
          }}
          nav {{
            display: flex;
            flex-wrap: wrap;
            gap: 0.6rem;
            margin-bottom: 1.5rem;
          }}
          nav a {{
            text-decoration: none;
            border: 1px solid var(--line);
            background: rgba(255,250,242,0.9);
            padding: 0.55rem 0.85rem;
            border-radius: 999px;
            font-size: 0.95rem;
          }}
          .panel {{
            background: rgba(255,250,242,0.94);
            border: 1px solid var(--line);
            border-radius: 1.15rem;
            box-shadow: 0 18px 40px rgba(20, 33, 61, 0.08);
            padding: 1.25rem;
          }}
          .grid {{
            display: grid;
            gap: 1rem;
          }}
          .grid.cols-2 {{
            grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
          }}
          label {{
            display: grid;
            gap: 0.35rem;
            font-weight: 600;
          }}
          input, textarea, select, button {{
            font: inherit;
          }}
          input, textarea, select {{
            width: 100%;
            padding: 0.75rem 0.85rem;
            border-radius: 0.75rem;
            border: 1px solid var(--line);
            background: white;
          }}
          button, .button {{
            display: inline-flex;
            align-items: center;
            justify-content: center;
            gap: 0.35rem;
            padding: 0.75rem 1rem;
            border-radius: 999px;
            border: 0;
            background: var(--accent);
            color: #fff9f1;
            text-decoration: none;
            cursor: pointer;
          }}
          .secondary {{
            background: var(--ink);
          }}
          .muted {{
            color: var(--muted);
          }}
          .result-card, .feed-item, .fixture-card {{
            border: 1px solid var(--line);
            border-radius: 1rem;
            padding: 1rem;
            background: rgba(255,255,255,0.75);
          }}
          .tag-list {{
            display: flex;
            flex-wrap: wrap;
            gap: 0.45rem;
            margin-top: 0.75rem;
          }}
          .tag {{
            border-radius: 999px;
            padding: 0.2rem 0.55rem;
            background: rgba(188,108,37,0.14);
            color: var(--accent);
            font-size: 0.85rem;
          }}
          .overlay {{
            position: fixed;
            inset: 0;
            display: flex;
            align-items: center;
            justify-content: center;
            background: rgba(20, 33, 61, 0.62);
            z-index: 20;
          }}
          .banner {{
            position: fixed;
            left: 1rem;
            right: 1rem;
            bottom: 1rem;
            padding: 1rem;
            background: var(--panel);
            border: 1px solid var(--line);
            border-radius: 1rem;
            z-index: 25;
          }}
          .warning {{
            color: var(--warn);
            font-weight: 700;
          }}
          iframe {{
            width: 100%;
            height: 360px;
            border: 1px solid var(--line);
            border-radius: 1rem;
            background: white;
          }}
          table {{
            width: 100%;
            border-collapse: collapse;
          }}
          th, td {{
            padding: 0.75rem;
            border-bottom: 1px solid var(--line);
            text-align: left;
          }}
        </style>
        {extra_head}
      </head>
      <body>
        <div class="shell">
          <header class="masthead">
            <div>
              <h1>{title}</h1>
              <p>Controlled fixture web for deterministic Synapse browser benchmarking.</p>
            </div>
            <div class="panel">
              <strong>Fixture App</strong><br />
              <span class="muted">{datetime.now(timezone.utc).isoformat()}</span>
            </div>
          </header>
          <nav>
            <a href="/">Index</a>
            <a href="/search">Search</a>
            <a href="/form">Form</a>
            <a href="/popup">Popup</a>
            <a href="/spa">SPA</a>
            <a href="/upload-download">Upload/Download</a>
            <a href="/iframe">Iframe</a>
            <a href="/lazy">Lazy Feed</a>
            <a href="/login">Login</a>
          </nav>
          {body}
        </div>
        <script>
          {script}
        </script>
      </body>
    </html>
    """
    return HTMLResponse(html)


@app.get("/", response_class=HTMLResponse)
async def fixture_index() -> HTMLResponse:
    cards = """
    <section class="grid cols-2">
      <article class="fixture-card"><h2>Search and Extraction</h2><p>Deterministic search results with authors, summaries, and tags.</p><a class="button" href="/search">Open Search Fixture</a></article>
      <article class="fixture-card"><h2>Form Filling</h2><p>Typed fields, selects, textarea, checkbox, and confirmation output.</p><a class="button" href="/form">Open Form Fixture</a></article>
      <article class="fixture-card"><h2>Popup Dismissal</h2><p>Cookie banner and modal overlay block core content until dismissed.</p><a class="button" href="/popup">Open Popup Fixture</a></article>
      <article class="fixture-card"><h2>SPA Navigation</h2><p>History API route changes without full page reload.</p><a class="button" href="/spa">Open SPA Fixture</a></article>
      <article class="fixture-card"><h2>Upload and Download</h2><p>File input workflow plus downloadable benchmark artifact.</p><a class="button" href="/upload-download">Open Upload/Download Fixture</a></article>
      <article class="fixture-card"><h2>Iframe Interaction</h2><p>Same-origin iframe with nested content and actions.</p><a class="button" href="/iframe">Open Iframe Fixture</a></article>
      <article class="fixture-card"><h2>Lazy Loading</h2><p>Bounded infinite-scroll style feed for extraction and navigation.</p><a class="button" href="/lazy">Open Lazy Feed</a></article>
      <article class="fixture-card"><h2>Login Continuation</h2><p>Cookie-backed session flow for durable authenticated profile tests.</p><a class="button" href="/login">Open Login Fixture</a></article>
    </section>
    """
    return render_page("Synapse Fixture Web", cards)


@app.get("/search", response_class=HTMLResponse)
async def search_page(q: str | None = None) -> HTMLResponse:
    query = (q or "").strip().lower()
    matches = [
        item for item in SEARCH_FIXTURES
        if not query or query in item["title"].lower() or query in item["summary"].lower() or query in item["authors"].lower()
    ]
    results = "".join(
        f"""
        <article class="result-card search-result" data-paper-title="{item["title"]}">
          <h2 class="paper-title">{item["title"]}</h2>
          <p><strong>Authors:</strong> <span class="paper-authors">{item["authors"]}</span></p>
          <p class="paper-summary">{item["summary"]}</p>
          <div class="tag-list">{"".join(f'<span class="tag">{tag}</span>' for tag in item["tags"])}</div>
        </article>
        """
        for item in matches
    ) or '<p class="warning">No results matched this deterministic fixture query.</p>'
    body = f"""
    <section class="panel">
      <form action="/search" method="get" class="grid">
        <label>Search query<input name="q" value="{q or ''}" placeholder="agents, benchmark, runtime" /></label>
        <button type="submit">Search fixture papers</button>
      </form>
    </section>
    <section class="panel" style="margin-top: 1rem;">
      <h2>Results</h2>
      <p class="muted" id="results-count">{len(matches)} result(s)</p>
      <div class="grid">{results}</div>
    </section>
    """
    return render_page("Search Fixture", body)


@app.get("/form", response_class=HTMLResponse)
async def form_page() -> HTMLResponse:
    body = """
    <section class="panel">
      <form action="/form/submit" method="post" class="grid cols-2">
        <label>Full name<input name="full_name" placeholder="Ava Operator" /></label>
        <label>Email<input name="email" type="email" placeholder="ava@example.com" /></label>
        <label>Workflow<select name="workflow">
          <option value="research">Research</option>
          <option value="extraction">Extraction</option>
          <option value="qa">QA</option>
        </select></label>
        <label>Priority<select name="priority">
          <option value="low">Low</option>
          <option value="normal">Normal</option>
          <option value="urgent">Urgent</option>
        </select></label>
        <label style="grid-column: 1 / -1;">Notes<textarea name="notes" rows="5" placeholder="Describe the benchmark scenario."></textarea></label>
        <label style="grid-column: 1 / -1;"><input type="checkbox" name="confirm" value="yes" /> Confirm fixture submission</label>
        <div style="grid-column: 1 / -1;"><button type="submit">Submit fixture form</button></div>
      </form>
    </section>
    """
    return render_page("Form Fixture", body)


@app.post("/form/submit", response_class=HTMLResponse)
async def form_submit(request: Request) -> HTMLResponse:
    payload = parse_qs((await request.body()).decode("utf-8"))
    full_name = payload.get("full_name", [""])[0]
    email = payload.get("email", [""])[0]
    workflow = payload.get("workflow", [""])[0]
    priority = payload.get("priority", [""])[0]
    notes = payload.get("notes", [""])[0]
    confirmed = "confirm" in payload
    body = f"""
    <section class="panel">
      <h2>Submission Received</h2>
      <table>
        <tr><th>Full name</th><td id="submitted-full-name">{full_name}</td></tr>
        <tr><th>Email</th><td id="submitted-email">{email}</td></tr>
        <tr><th>Workflow</th><td id="submitted-workflow">{workflow}</td></tr>
        <tr><th>Priority</th><td id="submitted-priority">{priority}</td></tr>
        <tr><th>Confirmed</th><td id="submitted-confirmed">{str(confirmed).lower()}</td></tr>
      </table>
      <p id="submitted-notes">{notes}</p>
      <a class="button" href="/form">Back to form</a>
    </section>
    """
    return render_page("Form Submission Fixture", body)


@app.get("/popup", response_class=HTMLResponse)
async def popup_page() -> HTMLResponse:
    body = """
    <section class="panel">
      <h2 id="popup-status">Primary content is blocked until popups are dismissed.</h2>
      <p>Use this page to test cookie banners, modal dismissal, and overlay handling.</p>
      <button id="open-secondary-modal" class="secondary" type="button">Open secondary modal</button>
    </section>
    <div id="consent-banner" class="banner">
      <p><strong>Cookie Banner:</strong> This deterministic fixture uses mock cookies for testing.</p>
      <button id="accept-cookies" type="button">Accept cookies</button>
    </div>
    <div id="blocking-modal" class="overlay">
      <div class="panel" style="width:min(30rem, calc(100vw - 2rem));">
        <h2>Benchmark Modal</h2>
        <p>This overlay blocks interactions until closed.</p>
        <button id="close-modal" type="button">Close modal</button>
      </div>
    </div>
    <div id="secondary-modal" class="overlay" hidden>
      <div class="panel" style="width:min(30rem, calc(100vw - 2rem));">
        <h2>Secondary Modal</h2>
        <p>A second modal to test repeated dismissal.</p>
        <button id="close-secondary-modal" type="button">Dismiss second modal</button>
      </div>
    </div>
    """
    script = """
    const status = document.getElementById("popup-status");
    const modal = document.getElementById("blocking-modal");
    const banner = document.getElementById("consent-banner");
    const second = document.getElementById("secondary-modal");

    document.getElementById("close-modal").addEventListener("click", () => {
      modal.remove();
      status.textContent = "Primary modal dismissed.";
    });
    document.getElementById("accept-cookies").addEventListener("click", () => {
      banner.remove();
      status.textContent = "Cookie banner dismissed.";
    });
    document.getElementById("open-secondary-modal").addEventListener("click", () => {
      second.hidden = false;
      status.textContent = "Secondary modal opened.";
    });
    document.getElementById("close-secondary-modal").addEventListener("click", () => {
      second.hidden = true;
      status.textContent = "All popups dismissed.";
    });
    """
    return render_page("Popup Fixture", body, script=script)


@app.get("/spa", response_class=HTMLResponse)
async def spa_page() -> HTMLResponse:
    body = """
    <section class="panel">
      <div style="display:flex; gap:0.6rem; flex-wrap:wrap;">
        <button data-route="overview" type="button">Overview</button>
        <button data-route="search" type="button">Search</button>
        <button data-route="settings" type="button">Settings</button>
      </div>
      <article id="spa-view" class="result-card" style="margin-top:1rem;"></article>
      <p class="muted">Current route: <span id="spa-route"></span></p>
    </section>
    """
    script = f"""
    const routes = {json.dumps(SPA_ROUTES)};
    const routeLabel = document.getElementById("spa-route");
    const view = document.getElementById("spa-view");

    function render(route) {{
      const data = routes[route] || routes.overview;
      routeLabel.textContent = route;
      view.innerHTML = `<h2>${{data.title}}</h2><p>${{data.body}}</p><button id="route-action" type="button">Run ${{route}} action</button>`;
      const action = document.getElementById("route-action");
      action.addEventListener("click", () => {{
        const marker = document.createElement("p");
        marker.id = "route-action-result";
        marker.textContent = `Action completed for ${{route}}`;
        view.appendChild(marker);
      }});
    }}

    function navigate(route) {{
      history.pushState({{ route }}, "", `/spa#${{route}}`);
      render(route);
    }}

    document.querySelectorAll("[data-route]").forEach((button) => {{
      button.addEventListener("click", () => navigate(button.dataset.route));
    }});

    window.addEventListener("popstate", (event) => {{
      render(event.state?.route || location.hash.replace("#", "") || "overview");
    }});

    render(location.hash.replace("#", "") || "overview");
    """
    return render_page("SPA Fixture", body, script=script)


@app.get("/upload-download", response_class=HTMLResponse)
async def upload_download_page() -> HTMLResponse:
    body = """
    <section class="grid cols-2">
      <article class="panel">
        <h2>Upload Fixture</h2>
        <label>Attach file<input id="benchmark-upload" type="file" multiple /></label>
        <button id="submit-upload" type="button">Submit uploaded files</button>
        <ul id="uploaded-files"></ul>
      </article>
      <article class="panel">
        <h2>Download Fixture</h2>
        <p>Download a deterministic artifact for Synapse benchmarking.</p>
        <a id="download-artifact" class="button" href="/downloads/report.csv">Download report.csv</a>
      </article>
    </section>
    """
    script = """
    const uploadInput = document.getElementById("benchmark-upload");
    const output = document.getElementById("uploaded-files");
    const submit = document.getElementById("submit-upload");

    submit.addEventListener("click", () => {
      output.innerHTML = "";
      const files = Array.from(uploadInput.files || []);
      if (!files.length) {
        output.innerHTML = "<li>No file selected.</li>";
        return;
      }
      files.forEach((file, index) => {
        const item = document.createElement("li");
        item.className = "uploaded-file";
        item.dataset.filename = file.name;
        item.textContent = `${index + 1}. ${file.name} (${file.size} bytes)`;
        output.appendChild(item);
      });
    });
    """
    return render_page("Upload and Download Fixture", body, script=script)


@app.get("/downloads/report.csv")
async def download_report() -> Response:
    content = "id,title,status\n1,fixture benchmark,ready\n2,download validation,ready\n"
    headers = {"Content-Disposition": 'attachment; filename="report.csv"'}
    return Response(content=content, media_type="text/csv", headers=headers)


@app.get("/iframe", response_class=HTMLResponse)
async def iframe_page() -> HTMLResponse:
    body = """
    <section class="panel">
      <h2>Outer Fixture</h2>
      <p>Interact with the same-origin iframe below to validate iframe traversal and action execution.</p>
      <iframe id="benchmark-frame" src="/iframe/child" title="Fixture iframe"></iframe>
    </section>
    """
    return render_page("Iframe Fixture", body)


@app.get("/iframe/child", response_class=HTMLResponse)
async def iframe_child() -> HTMLResponse:
    body = """
    <section class="panel">
      <h2 id="child-title">Iframe Search</h2>
      <label>Query<input id="iframe-query" placeholder="agents" /></label>
      <button id="iframe-run" type="button">Search inside iframe</button>
      <div id="iframe-results" class="grid" style="margin-top:1rem;"></div>
    </section>
    """
    script = f"""
    const source = {json.dumps(SEARCH_FIXTURES)};
    document.getElementById("iframe-run").addEventListener("click", () => {{
      const query = document.getElementById("iframe-query").value.toLowerCase().trim();
      const results = document.getElementById("iframe-results");
      const matches = source.filter((item) => !query || item.title.toLowerCase().includes(query) || item.summary.toLowerCase().includes(query));
      results.innerHTML = matches.map((item) => `<article class="result-card"><h3>${{item.title}}</h3><p>${{item.summary}}</p></article>`).join("") || "<p>No iframe results.</p>";
    }});
    """
    return render_page("Iframe Child Fixture", body, script=script)


@app.get("/lazy", response_class=HTMLResponse)
async def lazy_page() -> HTMLResponse:
    initial = LAZY_ITEMS[:6]
    cards = "".join(
        f'<article class="feed-item lazy-item"><h2>{item["title"]}</h2><p>{item["excerpt"]}</p></article>'
        for item in initial
    )
    body = f"""
    <section class="panel">
      <h2>Lazy Feed</h2>
      <p class="muted">Scroll or press load more to append deterministic feed entries.</p>
      <div id="lazy-feed" class="grid">{cards}</div>
      <div style="margin-top:1rem; display:flex; gap:0.6rem;">
        <button id="load-more" type="button">Load more</button>
        <span id="lazy-status" class="muted">Loaded {len(initial)} of {len(LAZY_ITEMS)} items.</span>
      </div>
    </section>
    """
    script = f"""
    const items = {json.dumps(LAZY_ITEMS)};
    let cursor = 6;
    const feed = document.getElementById("lazy-feed");
    const status = document.getElementById("lazy-status");
    const appendItems = () => {{
      const next = items.slice(cursor, cursor + 6);
      next.forEach((item) => {{
        const article = document.createElement("article");
        article.className = "feed-item lazy-item";
        article.innerHTML = `<h2>${{item.title}}</h2><p>${{item.excerpt}}</p>`;
        feed.appendChild(article);
      }});
      cursor += next.length;
      status.textContent = `Loaded ${{Math.min(cursor, items.length)}} of ${{items.length}} items.`;
    }};
    document.getElementById("load-more").addEventListener("click", appendItems);
    window.addEventListener("scroll", () => {{
      const nearBottom = window.innerHeight + window.scrollY >= document.body.offsetHeight - 200;
      if (nearBottom && cursor < items.length) appendItems();
    }});
    """
    return render_page("Lazy Loading Fixture", body, script=script)


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request) -> HTMLResponse:
    authenticated = request.cookies.get("fixture_session") == "authenticated"
    body = """
    <section class="panel">
      <h2>Login Fixture</h2>
      <form action="/auth/login" method="post" class="grid">
        <label>Email<input name="email" value="agent@example.com" /></label>
        <label>Password<input name="password" type="password" value="synapse" /></label>
        <button type="submit">Sign in</button>
      </form>
    </section>
    """
    if authenticated:
        body += """
        <section class="panel" style="margin-top:1rem;">
          <p id="session-status">Existing fixture session detected.</p>
          <a class="button" href="/account">Open account</a>
          <a class="button secondary" href="/auth/logout">Log out</a>
        </section>
        """
    return render_page("Login Fixture", body)


@app.post("/auth/login")
async def login_submit(request: Request) -> RedirectResponse:
    payload = parse_qs((await request.body()).decode("utf-8"))
    email = payload.get("email", ["agent@example.com"])[0]
    response = RedirectResponse("/account", status_code=303)
    response.set_cookie("fixture_session", "authenticated", httponly=False, max_age=3600)
    response.set_cookie("fixture_email", email, httponly=False, max_age=3600)
    return response


@app.get("/auth/logout")
async def logout() -> RedirectResponse:
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie("fixture_session")
    response.delete_cookie("fixture_email")
    return response


@app.get("/account", response_class=HTMLResponse)
async def account_page(request: Request) -> HTMLResponse:
    if request.cookies.get("fixture_session") != "authenticated":
        return RedirectResponse("/login", status_code=303)
    email = request.cookies.get("fixture_email", "agent@example.com")
    body = f"""
    <section class="panel">
      <h2>Fixture Account</h2>
      <p id="account-email"><strong>Email:</strong> {email}</p>
      <p id="account-status">Authenticated session continued successfully.</p>
      <div style="display:flex; gap:0.6rem;">
        <a class="button" href="/account/history">View history</a>
        <a class="button secondary" href="/auth/logout">Log out</a>
      </div>
    </section>
    """
    return render_page("Account Fixture", body)


@app.get("/account/history", response_class=HTMLResponse)
async def account_history(request: Request) -> HTMLResponse:
    if request.cookies.get("fixture_session") != "authenticated":
        return RedirectResponse("/login", status_code=303)
    body = """
    <section class="panel">
      <h2>Session History</h2>
      <table>
        <tr><th>Run</th><th>Status</th><th>Timestamp</th></tr>
        <tr><td>benchmark-001</td><td>completed</td><td>2026-03-26T08:00:00Z</td></tr>
        <tr><td>benchmark-002</td><td>completed</td><td>2026-03-26T08:15:00Z</td></tr>
      </table>
    </section>
    """
    return render_page("Account History Fixture", body)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/robots.txt", response_class=PlainTextResponse)
async def robots() -> str:
    return "User-agent: *\nAllow: /\n"

