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


# ── Low-quality content filtering ─────────────────────────────────────────────
# Ported from Odysseus's is_low_quality() in src/research_utils.py.
# Filters out boilerplate (cookie notices, paywalls, copyright footers)
# BEFORE they reach the LLM extraction stage, saving tokens and preventing
# hallucinated "findings" from junk content.

LOW_QUALITY_MARKERS = [
    # Phrases (not bare "cookie"/"copyright") so we catch boilerplate
    # like consent banners and footers without discarding legitimate findings
    # that merely discuss cookies or copyright as their subject.
    "cookie consent",
    "cookie banner",
    "cookie notice",
    "copyright notice",
    "copyright footer",
    "all rights reserved",
    # No-content indicators from extraction
    "insufficient to",
    "content is insufficient",
    "no substantive data",
    "does not contain",
    "not relevant to",
    "no relevant information",
    "unable to extract",
    "completely unrelated",
]


def is_low_quality(text: str) -> bool:
    """Check if fetched content is boilerplate or irrelevant.

    Returns True for cookie notices, paywall blocks, copyright footers,
    and "no relevant information" pages. Returns False for legitimate
    content (fail-open on errors).
    """
    try:
        if not isinstance(text, str) or not text:
            return True
        low = text.lower()
        return any(marker in low for marker in LOW_QUALITY_MARKERS)
    except Exception:
        return False  # fail open


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
            include_images=True,      # surface thumbnail images for the report
        )
        results = [
            {
                "url":     r.get("url", ""),
                "title":   r.get("title", ""),
                "snippet": r.get("content", ""),   # Tavily calls this "content"
                "score":   r.get("score", 0.0),
                "image":   r.get("image", ""),       # thumbnail URL for visual report
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
            title = r.get("title", url)
            # Filter out low-quality content (cookie notices, paywalls, etc.)
            # before it reaches the LLM extraction stage.
            if is_low_quality(content[:500]):
                return {"url": url, "title": title, "content": "", "error": "low_quality_content"}
            return {"url": url, "title": title, "content": content}
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

async def _handle_generate_html_report(args: dict[str, Any]) -> dict[str, Any]:
    from research.tools.reporting import generate_visual_report

    # Topic for the report title and filename.
    # The Jinja2 template uses {{ topic or Topic or '' }} to handle case-variant
    # CLI inputs where --input "Topic=..." stores as "Topic" (capital T) while
    # the YAML references "topic" (lowercase). The or-chain falls through
    # undefined variables to the available one.
    topic = args.get("topic")
    if not isinstance(topic, str) or not topic.strip():
        topic = None
    if topic is None:
        # Fallback: try to extract a title from the report content itself
        md = args.get("markdown") or ""
        if isinstance(md, str) and md.strip():
            from research.tools.reporting import _extract_report_title
            extracted, _ = _extract_report_title(md, "")
            if extracted and extracted.lower() not in {
                "report", "deep research report", "research",
                "executive summary", "summary", "overview",
            }:
                topic = extracted
    if topic is None:
        topic = "Research Report"
    md = args.get("markdown") or ""
    if not isinstance(md, str):
        md = ""
    run_id = args.get("run_id", "unknown")

    # Extract sources list from the workflow context.
    # Sources may arrive as:
    #   - [{url, title, image?}] — full objects (from select_sources stage)
    #   - ["https://...", ...] — flat URL strings (from decide_round.urls_fetched)
    #   - A string representation of a list (Jinja2 rendering of a Python list)
    # Normalize all formats into the object format the reporting module expects.
    import json as _json
    sources = args.get("sources") or []
    if isinstance(sources, str):
        # Jinja2 rendered the list as a string — try to parse it back
        try:
            sources = _json.loads(sources)
        except (_json.JSONDecodeError, ValueError):
            # Fall back to ast.literal_eval for Python-style lists
            import ast
            try:
                sources = ast.literal_eval(sources)
            except (ValueError, SyntaxError):
                sources = []
    if sources and isinstance(sources, list):
        if sources and isinstance(sources[0], str):
            sources = [{"url": u, "title": u} for u in sources if isinstance(u, str)]
    else:
        sources = []

    # Stats dict for the stats bar. Built from flat args since tool_call.args
    # only renders Jinja2 in top-level string values (not nested dicts).
    # Individual args: source_count, queries_used, iteration_num.
    stats = {}

    # URLs Analyzed — from source_count arg
    source_count_val = args.get("source_count")
    if source_count_val is not None:
        try:
            stats["URLs Analyzed"] = int(source_count_val)
        except (ValueError, TypeError):
            pass

    # Queries — from queries_used list (compute length)
    queries_used_val = args.get("queries_used")
    if isinstance(queries_used_val, list):
        stats["Queries"] = len(queries_used_val)
    elif isinstance(queries_used_val, str):
        try:
            parsed = _json.loads(queries_used_val)
            stats["Queries"] = len(parsed) if isinstance(parsed, list) else 0
        except (ValueError, TypeError):
            try:
                import ast
                parsed = ast.literal_eval(queries_used_val)
                stats["Queries"] = len(parsed) if isinstance(parsed, list) else 0
            except (ValueError, SyntaxError):
                pass

    # Rounds — from rounds arg (flat integer) or iteration_num (legacy)
    rounds_val = args.get("rounds")
    if rounds_val is not None:
        try:
            stats["Rounds"] = int(rounds_val)
        except (ValueError, TypeError):
            pass
    else:
        iteration_num_val = args.get("iteration_num")
        if iteration_num_val is not None:
            try:
                stats["Rounds"] = int(iteration_num_val)
            except (ValueError, TypeError):
                pass

    if not stats.get("Search") and run_id:
        stats["Search"] = "Tavily"

    # Category for report styling: product, comparison, howto, landscape, factcheck, or None
    category = args.get("category") or None

    html = generate_visual_report(
        question=topic,
        report_markdown=md,
        sources=sources,
        stats=stats,
        category=category,
    )

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
            "Convert a Markdown research report to a self-contained HTML file with "
            "editorial-quality styling: dark/light theme, aurora gradient, hero section, "
            "TOC sidebar, collapsible sources, print/export toolbar, and category-specific "
            "formatting. Writes to ./research-output/<topic>_<run_id>.html. "
            "Returns {filename, bytes}."
        ),
        permission=PermissionLevel.WORKSPACE,
        handler=_handle_generate_html_report,
        parameters={
            "topic":         {"type": "string",  "description": "Research topic (page title and filename)"},
            "markdown":      {"type": "string",  "description": "Full Markdown report text"},
            "run_id":        {"type": "string",  "description": "Run ID for attribution"},
            "sources":       {"type": "array",   "description": "Source list [{url, title, image?}] or flat URL strings for the collapsible sources panel"},
            "source_count":  {"type": "integer", "description": "Number of URLs analyzed (for stats bar)"},
            "queries_used":  {"type": "array",   "description": "List of search queries used (length used for stats bar)"},
            "rounds":        {"type": "integer",  "description": "Number of research rounds completed (for stats bar)"},
            "category":      {"type": "string",  "description": "Report category for styling: product, comparison, howto, landscape, factcheck"},
        },
    ))
