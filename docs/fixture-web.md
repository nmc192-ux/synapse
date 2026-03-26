# Fixture Web

The Synapse fixture web app provides deterministic browser workflows for
controlled runtime benchmarking.

## Coverage

The fixture app includes pages for:

- search and extraction
- form filling
- popup dismissal
- SPA navigation
- file upload and download
- iframe interaction
- lazy loading and infinite scroll
- login and session continuation

## Run Locally

From the repository root:

```bash
uvicorn synapse.fixtures.web:app --host 127.0.0.1 --port 8011 --reload
```

Open [http://127.0.0.1:8011](http://127.0.0.1:8011).

## Suggested Benchmark Targets

- `/search`
- `/form`
- `/popup`
- `/spa`
- `/upload-download`
- `/iframe`
- `/lazy`
- `/login`

## Notes

- The upload fixture is client-side and deterministic; it is intended for browser
  action validation, not file storage backends.
- The login flow sets reproducible fixture cookies for session restore testing.
- The iframe fixture is same-origin so Synapse can traverse it without cross-origin
  restrictions.
