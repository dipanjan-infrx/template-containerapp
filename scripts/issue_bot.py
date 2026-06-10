#!/usr/bin/env python3
"""GitHub Issue Fix Bot — reads an issue, asks Claude Sonnet (hosted on Azure AI
Foundry) to generate a minimal code fix + a pytest test, and writes them to disk
for the workflow to commit and open a PR.

Environment variables (set by .github/workflows/issue_bot.yml):
  ISSUE_NUMBER / ISSUE_TITLE / ISSUE_BODY   the GitHub issue
  ASKROMA_AZURE_CLAUDE_ENDPOINT             Azure AI Foundry base URL (…/anthropic/)
  ASKROMA_AZURE_CLAUDE_API_KEY              Foundry API key
  ASKROMA_AZURE_CLAUDE_MODEL                model name (e.g. claude-sonnet-4-…)
  GITHUB_WORKSPACE                          repo root (set by Actions)
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

# Only these top-level dirs (and specific root files) may be written by the bot.
_ALLOWED_PREFIXES: frozenset[str] = frozenset({"app", "tests"})
_ALLOWED_FILES: frozenset[str] = frozenset({"requirements.txt"})

_DELIMITER_START = "<<<ISSUE_START>>>"
_DELIMITER_END = "<<<ISSUE_END>>>"

# Files sent to the model as context so it understands the codebase.
_CONTEXT_FILES = ["app/main.py", "tests/test_health.py", "requirements.txt", "Dockerfile"]
_MAX_FILE_CHARS = 3_000
_MAX_TOTAL_CHARS = 18_000


def validate_file_path(raw_path: str, workspace: Path) -> Path:
    """Resolve raw_path under workspace and reject traversal / disallowed dirs."""
    if not raw_path or not raw_path.strip():
        raise ValueError("Empty file path")
    p = Path(raw_path)
    if p.is_absolute():
        raise ValueError(f"Absolute paths are not allowed: {raw_path!r}")
    resolved = (workspace / p).resolve()
    try:
        rel = resolved.relative_to(workspace.resolve())
    except ValueError:
        raise ValueError(f"Path traversal detected: {raw_path!r}")
    # Allow specific root-level files (e.g. requirements.txt for dependencies).
    if str(rel) in _ALLOWED_FILES:
        return resolved
    top = rel.parts[0] if rel.parts else ""
    if top not in _ALLOWED_PREFIXES:
        raise ValueError(
            f"Writes to '{top}/' are not permitted. "
            f"Allowed: {sorted(_ALLOWED_PREFIXES)} + {sorted(_ALLOWED_FILES)}"
        )
    return resolved


def sanitize_issue_body(body: str) -> str:
    """Wrap the issue body in delimiters (prompt-injection defence)."""
    safe = body.replace(_DELIMITER_START, "").replace(_DELIMITER_END, "")
    return f"{_DELIMITER_START}\n{safe}\n{_DELIMITER_END}"


def parse_llm_response(raw: str) -> dict:
    """Parse Claude's JSON response, robust to fences/prose around the object."""
    stripped = raw.strip()
    candidates: list[str] = []
    fence = re.search(r"```(?:json)?\s*\n?(.*?)```", stripped, re.DOTALL)
    if fence:
        candidates.append(fence.group(1).strip())
    first, last = stripped.find("{"), stripped.rfind("}")
    if first != -1 and last > first:
        candidates.append(stripped[first : last + 1])
    candidates.append(stripped)

    last_exc: Exception = ValueError("Empty LLM response")
    for cand in candidates:
        if not cand:
            continue
        try:
            parsed = json.loads(cand)
        except json.JSONDecodeError as exc:
            last_exc = exc
            continue
        if isinstance(parsed, dict) and "files" in parsed and "explanation" in parsed:
            parsed.setdefault("test_code", "")
            return parsed
    raise ValueError(f"Could not parse a valid fix object from model output: {last_exc}")


def _load_context(workspace: Path) -> str:
    parts: list[str] = []
    total = 0
    for rel in _CONTEXT_FILES:
        fpath = workspace / rel
        if not fpath.exists():
            continue
        content = fpath.read_text(encoding="utf-8", errors="replace")[:_MAX_FILE_CHARS]
        lang = "python" if rel.endswith(".py") else ""
        snippet = f"### {rel}\n```{lang}\n{content}\n```"
        if total + len(snippet) > _MAX_TOTAL_CHARS:
            break
        parts.append(snippet)
        total += len(snippet)
    return "\n\n".join(parts)


def _build_prompt(issue_number: int, title: str, body: str, context: str) -> str:
    safe_body = sanitize_issue_body(body)
    return (
        "OUTPUT FORMAT — MANDATORY:\n"
        "Your entire response must be a single raw JSON object. Nothing else.\n"
        "No analysis, no markdown fences. Start with '{' and end with '}'.\n\n"
        "You are an expert Python developer maintaining a small FastAPI service that\n"
        "runs as an Azure Container App. A GitHub issue was opened. Produce:\n"
        "1. Minimal, targeted code changes that address the issue.\n"
        "2. A pytest test verifying the change (self-contained, all imports included).\n\n"
        "JSON schema (follow exactly):\n"
        "{\n"
        '  "files": [{"path": "app/main.py", "content": "<COMPLETE file after fix>"}],\n'
        '  "test_code": "<imports + pytest test function(s) as one string>",\n'
        '  "explanation": "<2-3 sentence summary of what changed and why>"\n'
        "}\n\n"
        "Rules:\n"
        "- Paths must be relative and inside app/ or tests/, plus you MAY edit the\n"
        "  root file requirements.txt (never absolute or ../).\n"
        "- content must be the COMPLETE file after the fix, not a diff.\n"
        "- DEPENDENCIES: if your code imports any package NOT already in\n"
        "  requirements.txt, you MUST include an updated requirements.txt in files\n"
        "  with the new package pinned — otherwise the app fails to deploy. Common\n"
        "  example: using FastAPI Form(...) requires python-multipart.\n"
        "- test functions must be named test_* so pytest discovers them.\n"
        "- If no code change is needed, return \"files\": [] and explain why.\n"
        "- Keep changes minimal; do not refactor unrelated code.\n\n"
        f"Issue #{issue_number}: {title}\n\n"
        f"Issue body:\n{safe_body}\n\n"
        f"Codebase context:\n\n{context}"
    )


def main() -> None:
    num_str = os.environ.get("ISSUE_NUMBER", "")
    if not num_str.isdigit():
        print(f"[bot] ERROR: ISSUE_NUMBER must be an integer, got {num_str!r}", file=sys.stderr)
        sys.exit(1)
    issue_number = int(num_str)
    title = os.environ.get("ISSUE_TITLE", f"Issue #{issue_number}")
    body = os.environ.get("ISSUE_BODY", "")
    endpoint = os.environ["ASKROMA_AZURE_CLAUDE_ENDPOINT"]
    api_key = os.environ["ASKROMA_AZURE_CLAUDE_API_KEY"]
    model = os.environ["ASKROMA_AZURE_CLAUDE_MODEL"]
    workspace = Path(os.environ.get("GITHUB_WORKSPACE", Path(__file__).resolve().parents[1]))

    from anthropic import AnthropicFoundry  # imported lazily so the module is testable

    client = AnthropicFoundry(api_key=api_key, base_url=endpoint)
    prompt = _build_prompt(issue_number, title, body, _load_context(workspace))

    print(f"[bot] Calling Azure Claude ({model}) for issue #{issue_number}…")
    response = client.messages.create(
        model=model,
        system=(
            "You are a senior Python engineer. OUTPUT ONLY a single raw JSON object — "
            "no prose, no markdown fences. Start with '{' and end with '}'."
        ),
        messages=[{"role": "user", "content": prompt}],
        max_tokens=4096,
        temperature=0.1,
    )
    raw = response.content[0].text if response.content else ""
    result = parse_llm_response(raw)

    written: list[str] = []
    for spec in result["files"]:
        fpath = validate_file_path(spec["path"], workspace)
        fpath.parent.mkdir(parents=True, exist_ok=True)
        fpath.write_text(spec["content"], encoding="utf-8")
        written.append(spec["path"])
        print(f"[bot] Wrote {spec['path']}")

    test_code = (result.get("test_code") or "").strip()
    if test_code:
        test_path = workspace / f"tests/test_issue_{issue_number}.py"
        header = f'"""Auto-generated test for issue #{issue_number}: {title}"""\n\n'
        test_path.write_text(header + test_code + "\n", encoding="utf-8")
        print(f"[bot] Wrote tests/test_issue_{issue_number}.py")

    out = workspace / "bot_output"
    out.mkdir(exist_ok=True)
    (out / "explanation.txt").write_text(result["explanation"], encoding="utf-8")
    (out / "changed_files.txt").write_text("\n".join(written) if written else "(none)", encoding="utf-8")
    print(f"[bot] Explanation: {result['explanation']}")
    print(f"[bot] Files changed: {written or ['(none)']}")


if __name__ == "__main__":
    main()
