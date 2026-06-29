# Engagement-Badge Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Thread `engagement_label` from the raw community/social search-tool results through to the HTML report's Sources panel so engagement badges render by design instead of by accident.

**Architecture:** A new non-LLM `tool_call` stage (`collect_source_manifest`) inside the research subagent joins each round's `select_sources.selected_urls` with that round's raw tool results by normalized URL, attaches `engagement_label`, accumulates the manifest across rounds via `carry_forward`, and the parent's `generate_html` reads the final manifest instead of the compressed `decide_round.urls_fetched` string list. No LLM is asked to copy engagement fields.

**Tech Stack:** Python 3.11+, stdlib `urllib.parse`/`json`/`ast`, PyYAML, pytest + pytest-asyncio, Armature YAML workflow harness.

## Global Constraints

- Run tests with `python -m pytest` from the worktree root (NOT bare `pytest`) so the worktree's `research/` resolves first on `sys.path`. Baseline: **82 passed**.
- Run armature via `python -m armature.cli validate|run ...` from the worktree root (NOT the `armature` console script) for the same reason.
- Do NOT `pip install -e .` — the `.venv` has a stale armature 0.0.1 and resolution fails; the anaconda env already has armature + tavily + praw + pytest. `python -m pytest` works as-is.
- Tool-module pattern: `async def _handle_x(args: dict) -> dict` + `register(registry)` using `ToolDescriptor` / `PermissionLevel` from `armature.permissions.permissions` and `armature.registry.registry`. Graceful degradation: **never raise**; on any exception return `{..., error: str(exc)}`.
- Manifest entry shape is exactly `{url, title, engagement_label?, image?}` (YAGNI — only what `reporting.py` consumes). `engagement_label` is `None`/absent for web (Tavily) and YouTube-transcript sources, which have no native engagement signal → no badge for those, which is correct.
- `engagement_label` field name is identical across `communities.py` (HN/Polymarket/GitHub) and `social.py` (Reddit) — read it verbatim; do not recompute.
- Armature `tool_call.args` only renders Jinja2 in **top-level string values** (nested dict values are NOT rendered). Every arg to `build_source_manifest` is a string template.
- Armature promotes a carried dot-path `decide_round.urls_fetched` to a **top-level** `urls_fetched` in the next iteration's context (verified by `armature/tests/runtime/test_loop_iteration.py::test_loop_carry_forward_top_level_merge`). The same mechanism promotes `collect_source_manifest.sources_manifest` → top-level `sources_manifest`. Carry-forward keys must also be declared in `research-round.yaml` `contracts.inputs` (mirroring `urls_fetched`).
- `contracts.output_max_chars` is **unset** in `research-round.yaml`, so `_maybe_truncate` is a no-op and tool-call dict outputs are NOT truncated. Do NOT set `output_max_chars` on the new stage (match sibling tool-call stages `run_searches`, `run_hn_search`, etc.).
- The loop stage's final result is the subagent's full `{stage_id: output}` dict, so the parent can reference `{{ deep_research_round.collect_source_manifest.sources_manifest }}`.

---

## File Structure

| File | Responsibility | Action |
|------|----------------|--------|
| `research/tools/manifest.py` | `build_source_manifest` tool: parse inputs, build URL→engagement index, join with selected URLs, accumulate across rounds, degrade gracefully. | Create |
| `tests/tools/test_manifest.py` | Unit tests for helpers, handler, and `register`. | Create |
| `workflows/research-round.yaml` | Add `collect_source_manifest` stage, register `research.tools.manifest` module, declare `sources_manifest` carry-forward input. | Modify |
| `workflows/research-analyst.yaml` | Add `collect_source_manifest.sources_manifest` to loop `carry_forward`; repoint `generate_html.sources` at the manifest. | Modify |
| `tests/workflows/test_structure.py` | Structural assertions for the new wiring. | Modify |

---

## Task 1: `manifest.py` helpers — URL normalization, defensive parser, result walker

**Files:**
- Create: `research/tools/manifest.py`
- Test: `tests/tools/test_manifest.py`

**Interfaces:**
- Produces: `_normalize_url(url: str) -> str`, `_parse_list(value: Any) -> list`, `_iter_engagement_items(payload: Any) -> Iterator[tuple[str, str]]`. These are consumed by Task 2's `_handle_build_source_manifest`.

- [ ] **Step 1: Write the failing tests**

Create `tests/tools/test_manifest.py`:

```python
"""Tests for research/tools/manifest.py helpers."""
from research.tools.manifest import _normalize_url, _parse_list, _iter_engagement_items


def test_normalize_url_lowercases_scheme_and_host():
    assert _normalize_url("HTTPS://Example.COM/Foo") == "https://example.com/Foo"


def test_normalize_url_strips_trailing_slash_and_fragment():
    assert _normalize_url("https://example.com/foo/") == "https://example.com/foo"
    assert _normalize_url("https://example.com/foo#bar") == "https://example.com/foo"


def test_normalize_url_preserves_query():
    assert _normalize_url("https://example.com/foo?x=1&y=2") == "https://example.com/foo?x=1&y=2"


def test_normalize_url_empty_and_non_string():
    assert _normalize_url("") == ""
    assert _normalize_url(None) == ""
    assert _normalize_url(123) == ""


def test_parse_list_passes_through_actual_list():
    assert _parse_list([1, 2, 3]) == [1, 2, 3]


def test_parse_list_parses_json_string():
    assert _parse_list('[{"url": "a"}, {"url": "b"}]') == [{"url": "a"}, {"url": "b"}]


def test_parse_list_parses_python_repr_string():
    assert _parse_list("[{'url': 'a'}]") == [{"url": "a"}]


def test_parse_list_empty_and_garbage_to_empty_list():
    assert _parse_list("") == []
    assert _parse_list("   ") == []
    assert _parse_list("not a list at all") == []
    assert _parse_list('{"key": "value"}') == []  # valid JSON but not a list
    assert _parse_list(42) == []


def test_iter_engagement_items_per_query_wrapper():
    payload = [
        {"query": "rust", "results": [
            {"url": "https://example.com/1", "engagement_label": "★ 100"},
            {"url": "https://example.com/2", "engagement_label": "▲ 5"},
        ]},
    ]
    pairs = list(_iter_engagement_items(payload))
    assert ("https://example.com/1", "★ 100") in pairs
    assert ("https://example.com/2", "▲ 5") in pairs


def test_iter_engagement_items_single_dict():
    payload = {"query": "rust", "results": [
        {"url": "https://example.com/1", "engagement_label": "★ 100"},
    ]}
    assert ("https://example.com/1", "★ 100") in list(_iter_engagement_items(payload))


def test_iter_engagement_items_skips_items_without_label():
    payload = [
        {"query": "rust", "results": [
            {"url": "https://example.com/1", "engagement_label": "★ 100"},
            {"url": "https://example.com/2"},  # no engagement_label (web/Tavily)
        ]},
    ]
    pairs = list(_iter_engagement_items(payload))
    assert pairs == [("https://example.com/1", "★ 100")]


def test_iter_engagement_items_handles_flat_list():
    # YouTube transcripts arrive as a flat list of dicts with no 'results' wrapper
    # and no engagement_label -> nothing yielded.
    payload = [{"url": "https://youtube.com/watch?v=1", "video_id": "1", "transcript": "..."}]
    assert list(_iter_engagement_items(payload)) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/tools/test_manifest.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'research.tools.manifest'`.

- [ ] **Step 3: Write minimal implementation**

Create `research/tools/manifest.py`:

```python
"""Source-manifest builder: joins selected URLs with raw tool results to attach
engagement labels for the HTML report's Sources panel.

Defensive by design — never raises; garbled inputs degrade to empty lists so a
tool failure can never crash a research run.
"""
from __future__ import annotations

import ast
import json
import urllib.parse
from typing import Any, Iterator


def _normalize_url(url: str) -> str:
    """Normalize a URL for matching/dedup: lowercase scheme + host, strip the
    trailing slash, and drop the fragment. The query string is preserved. Empty
    or non-string input returns "".
    """
    if not isinstance(url, str):
        return ""
    s = url.strip()
    if not s:
        return ""
    try:
        parsed = urllib.parse.urlsplit(s)
    except ValueError:
        return s.lower().rstrip("/")
    scheme = (parsed.scheme or "").lower()
    netloc = (parsed.netloc or "").lower()
    path = parsed.path.rstrip("/") or "/"
    return urllib.parse.urlunsplit((scheme, netloc, path, parsed.query, ""))


def _parse_list(value: Any) -> list:
    """Coerce a Jinja-rendered list arg back to a list. Actual lists pass through;
    strings are parsed via json.loads then ast.literal_eval; anything else (or
    parse failure, or a non-list JSON value) becomes []. Never raises.
    """
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return []
        try:
            parsed = json.loads(s)
            return parsed if isinstance(parsed, list) else []
        except (json.JSONDecodeError, ValueError):
            try:
                parsed = ast.literal_eval(s)
                return parsed if isinstance(parsed, list) else []
            except (ValueError, SyntaxError):
                return []
    return []


def _iter_engagement_items(payload: Any) -> Iterator[tuple[str, str]]:
    """Yield (url, engagement_label) pairs from a raw tool-result payload.

    Handles every shape produced by the search tools:
      - [{query, results: [{url, ..., engagement_label}]}, ...]  (web/hn/polymarket/github/reddit, per-query)
      - {query, results: [...]}                                   (reddit, single dict)
      - [{url, video_id, transcript}, ...]                        (youtube transcripts; no engagement_label -> skipped)
      - [{url, ..., engagement_label}, ...]                       (flat result items, no per-query wrapper)

    Only items with both a url and an engagement_label are yielded.
    """
    entries = payload if isinstance(payload, list) else [payload]
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        items = entry.get("results")
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict):
                    url = item.get("url")
                    label = item.get("engagement_label")
                    if url and label:
                        yield str(url), str(label)
        elif entry.get("url") and entry.get("engagement_label"):
            yield str(entry["url"]), str(entry["engagement_label"])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/tools/test_manifest.py -v`
Expected: PASS (11 tests).

- [ ] **Step 5: Commit**

```bash
git add research/tools/manifest.py tests/tools/test_manifest.py
git commit -m "feat: add manifest.py URL-normalization + result-walker helpers"
```

---

## Task 2: `build_source_manifest` handler + `register`

**Files:**
- Modify: `research/tools/manifest.py`
- Test: `tests/tools/test_manifest.py` (extend)

**Interfaces:**
- Consumes: `_normalize_url`, `_parse_list`, `_iter_engagement_items` (Task 1).
- Produces: `async def _handle_build_source_manifest(args: dict) -> dict` returning `{sources_manifest, count, error}`; `register(registry)` registering tool `build_source_manifest` (PermissionLevel.READ_ONLY). The handler is invoked by the `collect_source_manifest` stage wired in Task 3.

- [ ] **Step 1: Write the failing tests**

Append to `tests/tools/test_manifest.py`:

```python
# ── build_source_manifest handler ──────────────────────────────────────────────

import asyncio
from research.tools.manifest import _handle_build_source_manifest


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_manifest_attaches_engagement_from_community_sources():
    args = {
        "selected_urls": '[{"url": "https://github.com/rust-lang/rust", "title": "Rust"}, '
                         '{"url": "https://news.ycombinator.com/item?id=1", "title": "HN post"}]',
        "github_results": '[{"query": "rust", "results": [{"url": "https://github.com/rust-lang/rust", '
                           '"engagement_label": "★ 90,000"}]}]',
        "hn_results": '[{"query": "rust", "results": [{"url": "https://news.ycombinator.com/item?id=1", '
                       '"engagement_label": "▲ 200 · 50 comments"}]}]',
    }
    result = _run(_handle_build_source_manifest(args))
    assert result["error"] is None
    by_url = {m["url"]: m for m in result["sources_manifest"]}
    assert by_url["https://github.com/rust-lang/rust"]["engagement_label"] == "★ 90,000"
    assert by_url["https://news.ycombinator.com/item?id=1"]["engagement_label"] == "▲ 200 · 50 comments"


def test_manifest_web_source_has_no_engagement_label():
    args = {
        "selected_urls": '[{"url": "https://example.com/article", "title": "Article"}]',
        "web_results": '[{"query": "ai", "results": [{"url": "https://example.com/article", "title": "Article"}]}]',
    }
    result = _run(_handle_build_source_manifest(args))
    entry = result["sources_manifest"][0]
    assert entry["url"] == "https://example.com/article"
    assert not entry["engagement_label"]  # None -> no badge (correct for web sources)


def test_manifest_normalizes_url_when_joining():
    # selected URL has a trailing slash; raw result does not — must still join.
    args = {
        "selected_urls": '[{"url": "https://github.com/rust-lang/rust/", "title": "Rust"}]',
        "github_results": '[{"query": "rust", "results": [{"url": "https://github.com/rust-lang/rust", '
                           '"engagement_label": "★ 90,000"}]}]',
    }
    result = _run(_handle_build_source_manifest(args))
    assert result["sources_manifest"][0]["engagement_label"] == "★ 90,000"


def test_manifest_accumulates_and_dedups_across_rounds():
    prior = '[{"url": "https://github.com/a", "title": "A", "engagement_label": "★ 10"}, ' \
            '{"url": "https://github.com/b", "title": "B", "engagement_label": "★ 20"}]'
    args = {
        "selected_urls": '[{"url": "https://github.com/b", "title": "B"}, '
                         '{"url": "https://github.com/c", "title": "C"}]',
        "prior_manifest": prior,
        "github_results": '[{"query": "q", "results": [{"url": "https://github.com/c", '
                           '"engagement_label": "★ 30"}]}]',
    }
    result = _run(_handle_build_source_manifest(args))
    urls = [m["url"] for m in result["sources_manifest"]]
    assert urls == ["https://github.com/a", "https://github.com/b", "https://github.com/c"]
    # The dup URL keeps the prior enriched entry (engagement already attached).
    by_url = {m["url"]: m for m in result["sources_manifest"]}
    assert by_url["https://github.com/b"]["engagement_label"] == "★ 20"
    assert by_url["https://github.com/c"]["engagement_label"] == "★ 30"
    assert result["count"] == 3


def test_manifest_empty_inputs():
    result = _run(_handle_build_source_manifest({}))
    assert result["sources_manifest"] == []
    assert result["count"] == 0
    assert result["error"] is None


def test_manifest_garbled_inputs_do_not_raise():
    args = {
        "selected_urls": "not valid json or python {{{",
        "github_results": "also garbage ::",
    }
    result = _run(_handle_build_source_manifest(args))
    assert result["sources_manifest"] == []
    assert result["error"] is None  # parse failures degrade to [], not exceptions


def test_manifest_preserves_image_and_title_from_selected():
    args = {
        "selected_urls": '[{"url": "https://example.com/a", "title": "A Title", "image": "https://img/a.png"}]',
    }
    result = _run(_handle_build_source_manifest(args))
    entry = result["sources_manifest"][0]
    assert entry["title"] == "A Title"
    assert entry["image"] == "https://img/a.png"


# ── register() ────────────────────────────────────────────────────────────────

def test_manifest_register_exposes_tool():
    from unittest.mock import MagicMock
    from research.tools.manifest import register
    registry = MagicMock()
    register(registry)
    calls = registry.register.call_args_list
    names = [c.args[0].name for c in calls]
    assert "build_source_manifest" in names
    descriptor = next(c.args[0] for c in calls if c.args[0].name == "build_source_manifest")
    # Permission and parameters are set on the ToolDescriptor.
    assert descriptor.parameters is not None
    assert "selected_urls" in descriptor.parameters
    assert "prior_manifest" in descriptor.parameters
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/tools/test_manifest.py -v`
Expected: the new tests FAIL — `ImportError: cannot import name '_handle_build_source_manifest'`.

- [ ] **Step 3: Write minimal implementation**

Append to `research/tools/manifest.py` (after the helpers, before the closing of the file):

```python
from armature.permissions.permissions import PermissionLevel
from armature.registry.registry import ToolDescriptor, ToolRegistry  # noqa: F401


async def _handle_build_source_manifest(args: dict[str, Any]) -> dict[str, Any]:
    """Join selected source URLs with raw search-tool results to build a source
    manifest carrying engagement labels for the HTML report. Accumulates across
    research rounds via `prior_manifest`. Never raises; garbled inputs degrade
    to empty lists and a tool failure can never crash a research run.
    """
    try:
        selected = _parse_list(args.get("selected_urls"))
        prior = _parse_list(args.get("prior_manifest"))

        # Build a normalized-URL -> engagement_label index from every raw result list.
        index: dict[str, str] = {}
        for key in ("web_results", "hn_results", "polymarket_results",
                    "github_results", "reddit_results", "youtube_results"):
            for url, label in _iter_engagement_items(_parse_list(args.get(key))):
                index.setdefault(_normalize_url(url), label)

        manifest: list[dict[str, Any]] = []
        seen: set[str] = set()

        # Seed with the prior manifest (already enriched in previous rounds).
        for entry in prior:
            if not isinstance(entry, dict):
                continue
            url = entry.get("url")
            if not url:
                continue
            nkey = _normalize_url(url)
            if nkey in seen:
                continue
            seen.add(nkey)
            manifest.append({
                "url": url,
                "title": entry.get("title") or url,
                "engagement_label": entry.get("engagement_label"),
                "image": entry.get("image"),
            })

        # Append this round's selected URLs, enriching engagement from the index.
        for sel in selected:
            if not isinstance(sel, dict):
                continue
            url = sel.get("url")
            if not url:
                continue
            nkey = _normalize_url(url)
            if nkey in seen:
                continue
            seen.add(nkey)
            manifest.append({
                "url": url,
                "title": sel.get("title") or url,
                "engagement_label": index.get(nkey),
                "image": sel.get("image"),
            })

        return {"sources_manifest": manifest, "count": len(manifest), "error": None}
    except Exception as exc:
        # Best-effort fallback: selected URLs without engagement if anything blew up.
        fallback: list[dict[str, Any]] = []
        for sel in _parse_list(args.get("selected_urls")):
            if isinstance(sel, dict) and sel.get("url"):
                fallback.append({
                    "url": sel["url"],
                    "title": sel.get("title") or sel["url"],
                    "engagement_label": None,
                    "image": sel.get("image"),
                })
        return {"sources_manifest": fallback, "count": len(fallback), "error": str(exc)}


def register(registry: ToolRegistry) -> None:
    """Register the source-manifest builder tool."""
    registry.register(ToolDescriptor(
        name="build_source_manifest",
        description=(
            "Join selected source URLs with raw search-tool results to build a "
            "source manifest carrying engagement labels for the HTML report's "
            "Sources panel. Accumulates across research rounds via prior_manifest. "
            "Never raises; garbled inputs degrade to empty lists. Returns "
            "{sources_manifest: [{url, title, engagement_label?, image?}], count, error?}."
        ),
        permission=PermissionLevel.READ_ONLY,
        handler=_handle_build_source_manifest,
        parameters={
            "selected_urls":      {"type": "string", "description": "Selected URL objects [{url, title, image?}] (Jinja-rendered list)"},
            "prior_manifest":     {"type": "string", "description": "Carried-forward manifest from the prior round (empty on round 1)"},
            "web_results":        {"type": "string", "description": "Tavily web search results (no engagement labels)"},
            "hn_results":         {"type": "string", "description": "Hacker News search results"},
            "polymarket_results": {"type": "string", "description": "Polymarket search results"},
            "github_results":     {"type": "string", "description": "GitHub search results"},
            "reddit_results":     {"type": "string", "description": "Reddit search results"},
            "youtube_results":    {"type": "string", "description": "YouTube transcript results"},
        },
    ))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/tools/test_manifest.py -v`
Expected: PASS (all manifest tests, helpers + handler + register).

- [ ] **Step 5: Run the full suite to confirm no regressions**

Run: `python -m pytest -q`
Expected: PASS (baseline 82 + new manifest tests, 0 failures).

- [ ] **Step 6: Commit**

```bash
git add research/tools/manifest.py tests/tools/test_manifest.py
git commit -m "feat: add build_source_manifest tool + register"
```

---

## Task 3: Wire `collect_source_manifest` stage into the subagent

**Files:**
- Modify: `workflows/research-round.yaml`
- Test: `tests/workflows/test_structure.py` (extend)

**Interfaces:**
- Consumes: `build_source_manifest` tool (Task 2); `select_sources.selected_urls` and the six search-stage outputs (all already in scope).
- Produces: a `collect_source_manifest` stage whose output `{sources_manifest, count, error}` is carried forward (Task 4) and read by `generate_html` (Task 4). Declares `sources_manifest` as a `contracts.inputs` carry-forward input.

- [ ] **Step 1: Write the failing tests**

Append to `tests/workflows/test_structure.py` (after the existing `research-round.yaml` tests, before the `research-analyst.yaml` section divider):

```python
def test_round_registers_manifest_module():
    spec = _load(ROUND)
    modules = [t["module"] for t in spec["tools"]]
    assert "research.tools.manifest" in modules


def test_round_has_collect_source_manifest_stage():
    ids = [s["id"] for s in _load(ROUND)["stages"]]
    assert "collect_source_manifest" in ids


def test_collect_source_manifest_depends_on_select_and_searches():
    spec = _load(ROUND)
    stage = next(s for s in spec["stages"] if s["id"] == "collect_source_manifest")
    deps = stage["depends_on"]
    for sid in ("select_sources", "run_searches", "run_hn_search",
                "run_polymarket_search", "run_github_search",
                "run_reddit_search", "fetch_youtube_transcripts"):
        assert sid in deps, f"collect_source_manifest missing dep {sid}"


def test_collect_source_manifest_calls_build_source_manifest():
    spec = _load(ROUND)
    stage = next(s for s in spec["stages"] if s["id"] == "collect_source_manifest")
    assert stage["tool_call"]["name"] == "build_source_manifest"


def test_round_declares_sources_manifest_input():
    names = [i["name"] for i in _load(ROUND)["contracts"]["inputs"]]
    assert "sources_manifest" in names
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/workflows/test_structure.py -v`
Expected: the 5 new tests FAIL (no manifest module / stage / input yet).

- [ ] **Step 3: Register the tool module**

In `workflows/research-round.yaml`, the `tools:` block is currently:

```yaml
tools:
  - module: research.tools.web
  - module: research.tools.social
  - module: research.tools.communities
  - module: research.tools.recency
```

Add the manifest module:

```yaml
tools:
  - module: research.tools.web
  - module: research.tools.social
  - module: research.tools.communities
  - module: research.tools.recency
  - module: research.tools.manifest
```

- [ ] **Step 4: Declare the `sources_manifest` carry-forward input**

In `workflows/research-round.yaml` `contracts.inputs`, the carry-forward block currently ends with:

```yaml
    - name: source_count       # Carry-forward: running total of sources consulted
```

Add one line after it:

```yaml
    - name: source_count       # Carry-forward: running total of sources consulted
    - name: sources_manifest   # Carry-forward: enriched source manifest from previous iteration
```

- [ ] **Step 5: Add the `collect_source_manifest` stage**

In `workflows/research-round.yaml`, insert this stage **immediately after the `select_sources` stage block ends** (i.e. right before the `# ── 4. Fetch full content ...` comment that precedes `fetch_articles`). The `select_sources` stage ends after its `output_schema` block; the next line is the `# ── 4.` comment.

```yaml
  # ── 3b. Build an enriched source manifest (engagement labels) for the report ──
  # Non-LLM tool call: joins select_sources.selected_urls with the raw search
  # results by normalized URL to attach engagement_label, and accumulates the
  # manifest across rounds via the carried-forward {{ sources_manifest }}.
  # Never raises; a tool error degrades to a smaller/empty manifest.
  - id: collect_source_manifest
    depends_on:
      [select_sources, run_searches, run_hn_search, run_polymarket_search,
       run_github_search, run_reddit_search, fetch_youtube_transcripts]
    fail_as_value: true
    tool_call:
      name: build_source_manifest
      args:
        selected_urls:      "{{ select_sources.selected_urls }}"
        prior_manifest:     "{{ sources_manifest }}"
        web_results:        "{{ run_searches }}"
        hn_results:         "{{ run_hn_search }}"
        polymarket_results: "{{ run_polymarket_search }}"
        github_results:     "{{ run_github_search }}"
        reddit_results:     "{{ run_reddit_search }}"
        youtube_results:    "{{ fetch_youtube_transcripts }}"
```

- [ ] **Step 6: Validate the spec**

Run: `python -m armature.cli validate workflows/research-round.yaml`
Expected: `✓ 'research-round' is valid`.

- [ ] **Step 7: Run the structural + full tests to verify they pass**

Run: `python -m pytest tests/workflows/test_structure.py -v && python -m pytest -q`
Expected: PASS (structural tests green; full suite green).

- [ ] **Step 8: Commit**

```bash
git add workflows/research-round.yaml tests/workflows/test_structure.py
git commit -m "feat: wire collect_source_manifest stage into research-round"
```

---

## Task 4: Carry-forward + repoint `generate_html` in the parent

**Files:**
- Modify: `workflows/research-analyst.yaml`
- Test: `tests/workflows/test_structure.py` (extend)

**Interfaces:**
- Consumes: `collect_source_manifest.sources_manifest` (Task 3's stage output).
- Produces: the parent's `generate_html.sources` arg bound to the final manifest, so `reporting.py` receives `{url, title, engagement_label?, image?}` entries and badges render by design.

- [ ] **Step 1: Write the failing tests**

Append to `tests/workflows/test_structure.py` (in the `research-analyst.yaml` section):

```python
def test_analyst_carries_sources_manifest():
    spec = _load(ANALYST)
    stage = next(s for s in spec["stages"] if s["id"] == "deep_research_round")
    carry = stage["loop"]["carry_forward"]
    assert "collect_source_manifest.sources_manifest" in carry


def test_generate_html_sources_bound_to_manifest():
    spec = _load(ANALYST)
    stage = next(s for s in spec["stages"] if s["id"] == "generate_html")
    sources_arg = stage["tool_call"]["args"]["sources"]
    assert "collect_source_manifest.sources_manifest" in sources_arg
    # The old compressed binding is gone.
    assert "decide_round.urls_fetched" not in sources_arg
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/workflows/test_structure.py -v`
Expected: the 2 new tests FAIL.

- [ ] **Step 3: Add the carry-forward entry**

In `workflows/research-analyst.yaml`, the `deep_research_round.loop.carry_forward` block is currently:

```yaml
      carry_forward:
        - decide_round.gaps
        - decide_round.key_themes
        - decide_round.coverage_score
        - decide_round.urls_fetched
        - decide_round.queries_used
        - decide_round.source_count
      iteration_var: _iteration
```

Add the manifest entry (keep `urls_fetched` — still used for fetch dedup):

```yaml
      carry_forward:
        - decide_round.gaps
        - decide_round.key_themes
        - decide_round.coverage_score
        - decide_round.urls_fetched
        - decide_round.queries_used
        - decide_round.source_count
        - collect_source_manifest.sources_manifest
      iteration_var: _iteration
```

- [ ] **Step 4: Repoint `generate_html.sources`**

In `workflows/research-analyst.yaml`, the `generate_html` stage `args` block is currently:

```yaml
    tool_call:
      name: generate_html_report
      args:
        topic: "{{ topic or Topic or '' }}"
        markdown: "{{ write_report.report }}"
        run_id: "{{ run_id }}"
        sources: "{{ deep_research_round.decide_round.urls_fetched }}"
        source_count: "{{ deep_research_round.decide_round.source_count }}"
        queries_used: "{{ deep_research_round.decide_round.queries_used }}"
        rounds: "{{ deep_research_round.decide_round.iteration_num }}"
        category: "{{ decompose_query.category }}"
```

Change **only** the `sources` line:

```yaml
    tool_call:
      name: generate_html_report
      args:
        topic: "{{ topic or Topic or '' }}"
        markdown: "{{ write_report.report }}"
        run_id: "{{ run_id }}"
        sources: "{{ deep_research_round.collect_source_manifest.sources_manifest }}"
        source_count: "{{ deep_research_round.decide_round.source_count }}"
        queries_used: "{{ deep_research_round.decide_round.queries_used }}"
        rounds: "{{ deep_research_round.decide_round.iteration_num }}"
        category: "{{ decompose_query.category }}"
```

- [ ] **Step 5: Validate the spec**

Run: `python -m armature.cli validate workflows/research-analyst.yaml`
Expected: `✓ 'research-analyst' is valid`.

- [ ] **Step 6: Run the structural + full tests to verify they pass**

Run: `python -m pytest tests/workflows/test_structure.py -v && python -m pytest -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add workflows/research-analyst.yaml tests/workflows/test_structure.py
git commit -m "feat: carry source manifest + bind generate_html to it"
```

---

## Task 5: End-to-end validation (specs + suite + dry-run)

**Files:** none (verification only).

- [ ] **Step 1: Validate both specs**

Run: `python -m armature.cli validate workflows/research-analyst.yaml && python -m armature.cli validate workflows/research-round.yaml`
Expected: both clean (`research-analyst` LOW risk; `research-round` HIGH risk — expected, unchanged in character from before this feature).

- [ ] **Step 2: Full test suite**

Run: `python -m pytest -q`
Expected: all green (baseline 82 + new manifest + structural tests, 0 failures).

- [ ] **Step 3: Dry-run with recency**

Run: `python -m armature.cli run workflows/research-analyst.yaml --input "topic=GLM-5.2 reception" --input "recency=30d" --dry-run`
Expected: validates inputs and DAG without executing LLM calls; no errors about missing tools or inputs. The `collect_source_manifest` stage and `sources_manifest` carry-forward are accepted by the DAG.

- [ ] **Step 4: Optional live run (skipped by default)**

A live `armature run` makes real LLM calls plus live network requests — significant cost and outward-facing. The dry-run + validate + full suite already verify the wiring. Left for the user to trigger when ready:

```bash
armature run workflows/research-analyst.yaml --input "topic=GLM-5.2 reception" --input "recency=30d"
```

Expected on a live run: community-source engagement badges (★ stars, ▲ points·comments, $vol·odds) appear in the HTML Sources panel; web/Tavily sources appear without badges (correct — no native engagement signal).

- [ ] **Step 5: Final commit (if any docs touch-ups)**

Only if the README's pipeline description or feature bullets need updating to mention the manifest. If no docs change is needed, skip this step. If needed:

```bash
git add README.md
git commit -m "docs: note engagement badges are sourced from the manifest pipeline"
```

---

## Self-Review

**1. Spec coverage:**
- Deterministic manifest tool → Tasks 1 (helpers) + 2 (handler + register).
- `collect_source_manifest` stage in subagent → Task 3.
- `tools:` registration → Task 3 Step 3.
- `sources_manifest` in `contracts.inputs` → Task 3 Step 4.
- carry-forward entry → Task 4 Step 3.
- `generate_html.sources` repoint → Task 4 Step 4.
- Unit tests (engagement attach, normalization join, accumulation/dedup, empty, garbled, image/title, register smoke) → Task 2.
- Structural tests (stage exists, deps, tool_call name, module registered, input declared, carry-forward, generate_html binding) → Tasks 3 + 4.
- Regression (reporting badge tests unchanged, full suite green) → Tasks 2 + 4 + 5.
- Validate both specs + dry-run → Task 5.
- Open Risk #1 (truncation): resolved — `contracts.output_max_chars` unset → no truncation; plan deliberately does NOT set `output_max_chars` (Global Constraints).
- Open Risk #2 (carry flatten): resolved — Global Constraints cite the armature test; `sources_manifest` declared in `contracts.inputs` (Task 3) mirroring `urls_fetched`.
- Open Risk #3 (per-query shape variance): handled — `_iter_engagement_items` walks `results` arrays, single-dict, and flat-list shapes (Task 1); YouTube transcripts (flat, no label) are skipped.
- Out-of-scope items (engagement_score/source_type in renderer, credibility_note display, select_sources ranking change, parent-side re-derivation, urls_fetched change) — none implemented; consistent with spec.

**2. Placeholder scan:** none — every code step contains complete, runnable code or exact find/replace edits. Step 5 Task 5 is conditional on a docs need and says so explicitly.

**3. Type consistency:** `_handle_build_source_manifest` returns `{sources_manifest, count, error}`; the stage reads `collect_source_manifest.sources_manifest`; the parent binds `generate_html.sources` to `deep_research_round.collect_source_manifest.sources_manifest`; `reporting.py` consumes `{url, title, engagement_label?, image?}` — the manifest entry shape produced by the handler. Field names (`engagement_label`, `sources_manifest`, `selected_urls`, `prior_manifest`) are identical across tasks. `_normalize_url` / `_parse_list` / `_iter_engagement_items` signatures match between Task 1 (defined) and Task 2 (consumed).