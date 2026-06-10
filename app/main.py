"""Fax Assigner — a deliberately tiny containerized web service.

The app is a prop for the platform demo: it just proves the pipeline works.
It reads its environment name from config (env var) — never hardcoded — so the
same image runs in dev and prod and simply reports where it is.
"""
import os

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

app = FastAPI(title="Fax Assigner")

# Config comes from the environment (set by the platform / pipeline), not code.
ENVIRONMENT = os.getenv("ENVIRONMENT", "local")


@app.get("/health")
def health() -> dict:
    """Liveness probe used by Container Apps and the demo."""
    return {"status": "ok", "env": ENVIRONMENT}


@app.get("/", response_class=HTMLResponse)
def home() -> str:
    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <title>Fax Assigner</title>
    <style>
      body {{ font-family: -apple-system, system-ui, sans-serif; display: grid;
              place-items: center; height: 100vh; margin: 0; background: #0f172a;
              color: #e2e8f0; }}
      .card {{ text-align: center; }}
      h1 {{ font-size: 2.5rem; margin: 0 0 .5rem; }}
      .env {{ font-size: 1.25rem; color: #38bdf8; }}
      code {{ background: #1e293b; padding: .15rem .4rem; border-radius: 4px; }}
    </style>
  </head>
  <body>
    <div class="card">
      <h1>📠 Fax Assigner</h1>
      <p class="env">running in <strong>{ENVIRONMENT}</strong></p>
      <p>health: <code>/health</code></p>
    </div>
  </body>
</html>"""
