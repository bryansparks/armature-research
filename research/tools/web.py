"""Research analyst tool module.

Exposes four tools to the Armature workflow:
  web_search           — search the web via Tavily (set TAVILY_API_KEY)
  fetch_url            — extract readable text from a URL via Tavily Extract API
  read_document        — read a local file (PDF, text, Markdown)
  generate_html_report — convert a Markdown report to a self-contained HTML file

Why Tavily:
  - Designed for AI agents: returns pre-extracted, LLM-ready text — not raw HTML
  - fetch_url uses Tavily Extract, which handles JS-rendered pages and bypasses
    common anti-scraping measures without needing a headless browser
  - One API key covers both search and extraction (TAVILY_API_KEY)

Pricing note: basic search = ~$0.001/query; advanced search with content = ~$0.004/query.
The workflow uses basic search for the fan-out stage (get URLs + snippets only)
then extract for the selected sources only — cost-efficient and selective.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

# Load .env from the project root (or any parent directory) so that
# TAVILY_API_KEY and OPENROUTER_API_KEY are available without pre-exporting them.
try:
    from dotenv import load_dotenv, find_dotenv
    load_dotenv(find_dotenv(usecwd=True), override=False)
except ImportError:
    pass  # python-dotenv not installed; env vars must be exported manually

from armature.permissions.permissions import PermissionLevel
from armature.registry.registry import ToolDescriptor, ToolRegistry


def _tavily_client():
    """Return a TavilyClient, raising clearly if the package or key is missing."""
    try:
        from tavily import TavilyClient
    except ImportError as exc:
        raise RuntimeError(
            "tavily-python is not installed. Run: pip install tavily-python"
        ) from exc
    api_key = os.environ.get("TAVILY_API_KEY", "")
    if not api_key:
        raise RuntimeError(
            "TAVILY_API_KEY environment variable is not set. "
            "Get a key at https://app.tavily.com"
        )
    return TavilyClient(api_key=api_key)


# ── Web search ─────────────────────────────────────────────────────────────────

async def _handle_web_search(args: dict[str, Any]) -> dict[str, Any]:
    """Search the web via Tavily and return URL, title, and snippet for each result."""
    query = args.get("query", "").strip()
    max_results = int(args.get("max_results", 5))
    if not query:
        return {"query": query, "results": [], "error": "empty query"}
    try:
        client = _tavily_client()
        # search_depth="basic" returns snippets only — fast and cheap.
        # The select_sources + fetch_url stages handle full content retrieval.
        response = client.search(
            query=query,
            max_results=max_results,
            search_depth="basic",
            include_answer=False,
        )
        results = [
            {
                "url":     r.get("url", ""),
                "title":   r.get("title", ""),
                "snippet": r.get("content", ""),   # Tavily calls this "content"
                "score":   r.get("score", 0.0),
            }
            for r in response.get("results", [])
        ]
        return {"query": query, "results": results}
    except Exception as exc:
        return {"query": query, "results": [], "error": str(exc)}


# ── URL content extraction ─────────────────────────────────────────────────────

async def _handle_fetch_url(args: dict[str, Any]) -> dict[str, Any]:
    """Extract readable text from a URL using Tavily Extract.

    Tavily Extract handles JavaScript-rendered pages and returns clean,
    LLM-ready text without needing a headless browser or HTML parsing.
    Falls back to a simple requests fetch if Extract fails.
    """
    url = args.get("url", "").strip()
    max_chars = int(args.get("max_chars", 12000))
    if not url:
        return {"url": url, "title": "", "content": "", "error": "empty url"}
    try:
        client = _tavily_client()
        response = client.extract(urls=[url])
        results = response.get("results", [])
        if results:
            r = results[0]
            content = (r.get("raw_content") or r.get("content") or "")[:max_chars]
            return {"url": url, "title": r.get("title", url), "content": content}
        # Tavily returned no results for this URL (paywall, bot-block, etc.)
        failed = response.get("failed_results", [])
        reason = failed[0].get("error", "no content extracted") if failed else "no content extracted"
        return {"url": url, "title": "", "content": "", "error": reason}
    except Exception as exc:
        return {"url": url, "title": "", "content": "", "error": str(exc)}


# ── Document reading ───────────────────────────────────────────────────────────

async def _handle_read_document(args: dict[str, Any]) -> dict[str, Any]:
    """Read a local file and return its text content."""
    path_str = args.get("path", "").strip()
    if not path_str:
        return {"filename": "", "content": "", "error": "empty path"}
    path = Path(path_str).expanduser().resolve()
    if not path.exists():
        return {"filename": path.name, "content": "", "error": f"file not found: {path}"}
    try:
        suffix = path.suffix.lower()
        if suffix == ".pdf":
            try:
                import pypdf
                reader = pypdf.PdfReader(str(path))
                text = "\n".join(page.extract_text() or "" for page in reader.pages)
            except ImportError:
                return {
                    "filename": path.name,
                    "content": "",
                    "error": "pypdf not installed — run: pip install pypdf",
                }
        else:
            text = path.read_text(encoding="utf-8", errors="replace")
        return {"filename": path.name, "content": text[:20000]}
    except Exception as exc:
        return {"filename": path.name, "content": "", "error": str(exc)}


# ── HTML report generation ─────────────────────────────────────────────────────

_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         max-width: 900px; margin: 40px auto; padding: 0 24px;
         color: #1a1a1a; line-height: 1.6; }}
  h1.report-title {{ font-size: 2.4rem; font-weight: 700;
                     border-bottom: 3px solid #2563eb; padding-bottom: 12px; margin-bottom: 4px; }}
  .report-date {{ font-size: 0.95rem; color: #6b7280; margin-top: 0; margin-bottom: 2rem; }}
  h2 {{ font-size: 1.4rem; margin-top: 2.5rem; color: #1d4ed8;
        border-bottom: 1px solid #e5e7eb; padding-bottom: 4px; }}
  h3 {{ font-size: 1.15rem; margin-top: 1.5rem; }}
  h4 {{ font-size: 1rem; margin-top: 1.25rem; color: #374151; }}
  a {{ color: #2563eb; }}
  code {{ background: #f3f4f6; padding: 1px 4px; border-radius: 3px; font-size: 0.9em; }}
  pre {{ background: #f3f4f6; padding: 12px; border-radius: 6px; overflow-x: auto; }}
  blockquote {{ border-left: 3px solid #93c5fd; margin: 0; padding-left: 16px; color: #374151; }}
  table {{ border-collapse: collapse; width: 100%; margin: 1rem 0; }}
  th, td {{ border: 1px solid #e5e7eb; padding: 8px 12px; text-align: left; }}
  th {{ background: #f9fafb; font-weight: 600; }}
  .recent-section {{ background: #eff6ff; border-left: 4px solid #2563eb;
                     border-radius: 0 6px 6px 0; padding: 16px 20px; margin: 1.5rem 0; }}
  .recent-section h2 {{ border: none; padding: 0; margin-top: 0; }}
  .ref-list {{ list-style: none; padding: 0; }}
  .ref-list li {{ margin-bottom: 1rem; padding-bottom: 1rem; border-bottom: 1px solid #e5e7eb; }}
  .ref-list li:last-child {{ border-bottom: none; margin-bottom: 0; padding-bottom: 0; }}
  .ref-list .ref-title {{ font-weight: 600; }}
  .ref-list .ref-desc {{ color: #374151; font-size: 0.9rem; margin-top: 2px; }}
  .meta {{ font-size: 0.85rem; color: #6b7280; margin-top: 2rem;
           border-top: 1px solid #e5e7eb; padding-top: 1rem; }}
</style>
</head>
<body>
<h1 class="report-title">{title}</h1>
<p class="report-date">Report generated: {report_date} &nbsp;·&nbsp; Run ID: {run_id}</p>
{body}
<div class="meta">Generated by Research Analyst · Run ID: {run_id} · {report_date}</div>
</body>
</html>
"""


def _markdown_to_html(md: str) -> str:
    """Convert Markdown to HTML. Uses `markdown` package if available."""
    try:
        import markdown
        return markdown.markdown(md, extensions=["tables", "fenced_code"])
    except ImportError:
        pass
    # Minimal fallback
    lines = []
    for line in md.splitlines():
        if line.startswith("### "):
            lines.append(f"<h3>{line[4:]}</h3>")
        elif line.startswith("## "):
            lines.append(f"<h2>{line[3:]}</h2>")
        elif line.startswith("# "):
            lines.append(f"<h1>{line[2:]}</h1>")
        elif line.startswith(("- ", "* ")):
            lines.append(f"<li>{line[2:]}</li>")
        elif line.strip():
            lines.append(f"<p>{line}</p>")
        else:
            lines.append("")
    return "\n".join(lines)


async def _handle_generate_html_report(args: dict[str, Any]) -> dict[str, Any]:
    import datetime
    topic = args.get("topic") or "Research Report"
    if not isinstance(topic, str):
        topic = "Research Report"
    md = args.get("markdown") or ""
    if not isinstance(md, str):
        md = ""
    run_id = args.get("run_id", "unknown")
    report_date = datetime.date.today().strftime("%B %-d, %Y")

    # Strip a leading h1/h2 title line if the LLM echoes the topic there —
    # the template already injects it as a styled h1.
    lines = md.splitlines()
    if lines and lines[0].lstrip().startswith(("# ", "## ")):
        md = "\n".join(lines[1:]).lstrip()

    body = _markdown_to_html(md)
    html = _HTML_TEMPLATE.format(title=topic, body=body, run_id=run_id, report_date=report_date)

    out_dir = Path("./research-output")
    out_dir.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r"[^\w\-]", "_", topic.lower())[:50]
    out_path = out_dir / f"{safe_name}_{run_id}.html"
    out_path.write_text(html, encoding="utf-8")

    return {"filename": str(out_path), "bytes": len(html)}


# ── Registration ───────────────────────────────────────────────────────────────

def register(registry: ToolRegistry) -> None:
    registry.register(ToolDescriptor(
        name="web_search",
        description=(
            "Search the web for a query string and return up to max_results results. "
            "Uses Tavily Search API (set TAVILY_API_KEY). "
            "Each result has url, title, snippet, and relevance score. "
            "Returns {query, results: [{url, title, snippet, score}], error?}."
        ),
        permission=PermissionLevel.READ_ONLY,
        handler=_handle_web_search,
        parameters={
            "query":       {"type": "string",  "description": "Search query string"},
            "max_results": {"type": "integer", "description": "Maximum number of results (default 5)"},
        },
    ))
    registry.register(ToolDescriptor(
        name="fetch_url",
        description=(
            "Extract readable text from a URL using Tavily Extract API. "
            "Handles JavaScript-rendered pages without a headless browser. "
            "Returns {url, title, content, error?}."
        ),
        permission=PermissionLevel.READ_ONLY,
        handler=_handle_fetch_url,
        parameters={
            "url":       {"type": "string",  "description": "URL to extract content from"},
            "max_chars": {"type": "integer", "description": "Maximum characters to return (default 12000)"},
        },
    ))
    registry.register(ToolDescriptor(
        name="read_document",
        description=(
            "Read a local file and return its text content. "
            "Supports plain text, Markdown, and PDF (requires pypdf). "
            "Returns {filename, content, error?}."
        ),
        permission=PermissionLevel.READ_ONLY,
        handler=_handle_read_document,
        parameters={
            "path": {"type": "string", "description": "Absolute or home-relative path to the file"},
        },
    ))
    registry.register(ToolDescriptor(
        name="generate_html_report",
        description=(
            "Convert a Markdown research report to a self-contained HTML file. "
            "Writes to ./research-output/<topic>_<run_id>.html. "
            "Returns {filename, bytes}."
        ),
        permission=PermissionLevel.WORKSPACE,
        handler=_handle_generate_html_report,
        parameters={
            "topic":    {"type": "string", "description": "Research topic (page title and filename)"},
            "markdown": {"type": "string", "description": "Full Markdown report text"},
            "run_id":   {"type": "string", "description": "Run ID for attribution"},
        },
    ))
