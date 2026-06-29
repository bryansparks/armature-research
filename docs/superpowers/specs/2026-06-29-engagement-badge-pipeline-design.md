# Engagement-Badge Pipeline — Design Spec

**Date:** 2026-06-29
**Status:** Approved (pre-implementation)
**Scope:** Fix the engagement-badge pipeline so community-source badges render in the HTML report by design, not by accident.

## Problem

The HTML report's Sources panel renders engagement badges (`★ stars`, `▲ points · comments`, `$vol · odds`) only when a source object carries `engagement_label`. A live run showed 1 of 14 sources got a badge. Root cause: `engagement_label` is produced by the search tools but is dropped before it reaches the renderer. Two compounding drop points:

1. **`select_sources` strips engagement** (`workflows/research-round.yaml:347`). Its `output_schema` requires only `{url, title, sub_question_index, credibility_note, image}`. The prompt tells the LLM to rank by engagement signals, but the schema gives it no field to *emit* those signals, so they're used for ranking and discarded.

2. **`decide_round.urls_fetched` is `string[]`** (`workflows/research-round.yaml:624`). `generate_html` is bound to `sources: "{{ deep_research_round.decide_round.urls_fetched }}"` — plain URL strings, deliberately compressed for cheap loop carry-forward / dedup. The `generate_html_report` handler (`research/tools/web.py:256`) promotes bare strings to `{"url": u, "title": u}`. No `engagement_label` reaches `reporting.py:1164`, so no badge.

The 1-of-14 badge was accidental: the `decide_round` LLM emitted a dict instead of a string for one entry despite the `string` schema, and the handler passed it through. Fragile luck, not design.

**Architectural tension:** Armature's loop intentionally compresses inter-round state to URL strings to stay token-cheap. Badges need rich per-source metadata at the *end*. The engagement data lives on raw tool results inside the subagent; the parent never sees them (the subagent result exposes only per-stage outputs, and raw results aren't carried out). So the fix must build a rich source list *inside the subagent* and thread it to the renderer without asking an LLM to copy engagement fields verbatim — that mechanical pass-off is exactly the failure mode being fixed.

## Chosen Approach

A deterministic, non-LLM `tool_call` stage joins `select_sources.selected_urls` with the raw tool results by URL, attaches `engagement_label`, accumulates the manifest across rounds via `carry_forward`, and the parent's `generate_html` reads the final manifest. No LLM copies engagement fields; the join is testable Python.

### Why not the alternatives

- **Enrich the `select_sources` schema** (add `engagement_label` to its `output_schema` + a manifest carry-forward): rejected — relies on the LLM copying `engagement_label` verbatim into its output, the same unreliable mechanical pass-off this fix targets. `guided_json` optional fields are silently dropped when omitted.
- **Re-derive badges in the renderer** (feed raw community-tool results to `generate_html` and join there): rejected — the subagent boundary hides inner tool results from the parent; `deep_research_round` exposes only per-stage outputs, not `run_hn_search` etc. Bubbling raw results out defeats the compression design worse than the chosen approach, and a URL join without a single source of truth is fragile.

## Architecture

One new tool, one new non-LLM stage, three small YAML edits. No LLM behavior change.

### New tool — `research/tools/manifest.py`

```
build_source_manifest(args) -> {sources_manifest, count, error?}
```

**Inputs** (each arrives as a Jinja-rendered string per armature's top-level-string-only `tool_call.args` rule, parsed defensively like `web.py:246-253`):

| arg | source | shape |
|-----|--------|-------|
| `selected_urls` | `select_sources.selected_urls` | `[{url, title, sub_question_index, credibility_note, image?}, …]` |
| `prior_manifest` | carried `{{ sources_manifest }}` (empty on iteration 1) | `[{url, title, engagement_label?, image?}, …]` |
| `web_results` | `run_searches` | `[{query, results:[{url, title, snippet, image?}]}, …]` (no engagement) |
| `hn_results` | `run_hn_search` | `[{query, results:[{url, title, engagement_label, …}]}, …]` |
| `polymarket_results` | `run_polymarket_search` | per-query; results carry `engagement_label` |
| `github_results` | `run_github_search` | per-query; results carry `engagement_label` |
| `reddit_results` | `run_reddit_search` | per-query; results carry `engagement_label` |
| `youtube_results` | `fetch_youtube_transcripts` | per-query; results carry `engagement_label` |

**Logic:**

1. Parse each input defensively: `json.loads(s)` → `ast.literal_eval(s)` → `[]` on failure. Never raise on parse.
2. Build a URL→`{engagement_label, image}` index by walking every raw result list's `results` arrays (and flat lists). Web/Tavily entries contribute `image` only (no `engagement_label`).
3. `_normalize_url(url)`: lowercase scheme + host, strip trailing slash, drop fragment; keep query string. Used for all matching/dedup.
4. Seed the output manifest with `prior_manifest` (already enriched from prior rounds). For each entry in `selected_urls`: if its normalized URL is already in the manifest, keep the existing (enriched) entry; else append `{url, title, engagement_label (from index, or absent), image}` — prefer the `selected_urls` entry's `title`/`image`, fill `engagement_label` from the index when the normalized URL matches.
5. Return `{sources_manifest: [...], count: len, error: None}`. On any exception, return `{sources_manifest: <best-effort list built so far, or selected_urls as {url,title} only>, count, error: str(exc)}`.

**Manifest entry shape (YAGNI — only what the renderer consumes):**
```
{url, title, engagement_label?, image?}
```
`engagement_label` is absent for web/Tavily sources (no native signal) → no badge for those, which is correct.

**Registration:** `register(registry)` registers `build_source_manifest` as `PermissionLevel.READ_ONLY` with a `parameters` schema for each input arg (all `type: string` since they arrive Jinja-rendered; description notes the parsed shape). Follow the `communities.py` registration pattern.

### New stage — `research-round.yaml`

```yaml
- id: collect_source_manifest
  depends_on:
    [select_sources, run_searches, run_hn_search, run_polymarket_search,
     run_github_search, run_reddit_search, fetch_youtube_transcripts]
  fail_as_value: true
  output_max_chars: 20000
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

- `depends_on` lists `select_sources` plus every search stage, so each `{{ <stage> }}` reference is in dependency scope (armature scopes Jinja refs by declared dependency).
- `fail_as_value: true` so a tool error cannot crash the run.
- `output_max_chars: 20000` guards against truncation of the manifest dict the parent later reads.
- `tools:` list in `research-round.yaml`: add `- module: research.tools.manifest`.

### Carry-forward — `research-analyst.yaml`

The `deep_research_round.loop.carry_forward` list gains one entry:

```yaml
carry_forward:
  - decide_round.gaps
  - decide_round.key_themes
  - decide_round.coverage_score
  - decide_round.urls_fetched
  - decide_round.queries_used
  - decide_round.source_count
  - collect_source_manifest.sources_manifest   # NEW
```

`decide_round.urls_fetched` stays (still used for cross-round fetch dedup). The manifest is additive.

Armature merges carried dot-paths to a top-level leaf name in the next iteration's context (verified by `tests/runtime/test_loop_iteration.py::test_loop_carry_forward_top_level_merge` — same mechanism that makes `{{ urls_fetched }}` resolve today). So `collect_source_manifest` references the prior manifest as `{{ sources_manifest }}`, and on iteration 1 it is undefined → renders empty → handler parses to `[]`.

### Parent binding — `research-analyst.yaml`

`generate_html` stage (line 502) changes its `sources` arg:

```yaml
sources: "{{ deep_research_round.collect_source_manifest.sources_manifest }}"
```

(replaces `{{ deep_research_round.decide_round.urls_fetched }}`).

The loop stage's final result is the subagent's full `{stage_id: output}` dict (armature `Harness.run` returns all stage outputs), so `deep_research_round.collect_source_manifest.sources_manifest` resolves. `source_count`, `queries_used`, `rounds`, `category` args are unchanged.

## Data Flow

```
run_searches / run_hn_search / run_github_search / run_polymarket_search /
  run_reddit_search / fetch_youtube_transcripts  ──┐
                                                   ├─► collect_source_manifest (tool_call, non-LLM)
select_sources.selected_urls  ────────────────────┘        join by normalized URL → attach engagement_label
                                                            merge + dedup vs prior {{ sources_manifest }}
prior {{ sources_manifest }} (carry_forward) ──►            ↓
                                              {sources_manifest: [{url,title,engagement_label?,image?}, …]}
                                                            ↓ carry_forward: collect_source_manifest.sources_manifest
next iteration ◄────────────────────────────────────────────┘
                                                            ↓ final iteration
parent generate_html.sources ◄── {{ deep_research_round.collect_source_manifest.sources_manifest }}
                                                            ↓
reporting.py sources panel: s["engagement_label"]  →  badges render by design
```

## Error Handling

- **Tool never raises.** Any parse/exception → best-effort manifest (selected_urls as `{url,title}` if nothing else) + `error` set; run continues.
- **Selected URL with no raw-result match** (LLM-hallucinated or snippet-only): entry still appears with `url`/`title`, no `engagement_label`, no badge. Not a failure.
- **`fail_as_value: true`** on the stage: a tool error degrades to an empty/small manifest rather than crashing the run; the renderer just shows fewer badges.
- **Skipped optional stages** (`run_reddit_search`/`fetch_youtube_transcripts` may be `_skipped` if `praw`/`youtube-transcript-api` are absent): their arg renders as the skipped-marker or empty → handler parses to `[]` → manifest built from the remaining sources. No special-casing needed beyond defensive parse.

## Testing

### Unit — `tests/tools/test_manifest.py` (new)

- Engagement attaches from each community source type (HN, GitHub, Polymarket, Reddit, YouTube) when its URL is selected.
- Web/Tavily-only selected URL → manifest entry with no `engagement_label` (absent, not empty string).
- `_normalize_url`: host case-insensitive, trailing slash, fragment dropped, query preserved.
- `prior_manifest` accumulation: round-2 selected URLs appended to round-1 manifest; cross-round dedup keeps the enriched entry when a URL repeats.
- Empty `selected_urls` + empty prior → empty manifest, `count: 0`, no error.
- Garbled string inputs (non-JSON) → graceful: parses to `[]`, returns best-effort manifest, `error` set, no exception raised.
- `register(registry)` smoke test: tool registered with correct name/permission/parameters (mirror the `communities.py` smoke test).

### Structural — `tests/workflows/test_structure.py` (extend)

- `collect_source_manifest` stage exists in `research-round.yaml`.
- `collect_source_manifest.sources_manifest` present in `research-analyst.yaml` loop `carry_forward`.
- `generate_html.sources` bound to `deep_research_round.collect_source_manifest.sources_manifest` (not `decide_round.urls_fetched`).
- `research.tools.manifest` listed in `research-round.yaml` `tools:`.

### Regression

- `tests/tools/test_reporting.py` badge tests unchanged and green.
- Full suite (`python -m pytest -q`) stays green (baseline 82).

### Live re-run (optional / costly)

`armature run workflows/research-analyst.yaml --input "topic=GLM-5.2 reception" --input "recency=30d"` → confirm community-source badges appear in the HTML Sources panel. Skipped by default; left for the user.

## Open Risks (confirm during planning/implementation)

1. **`output_max_chars` / truncation of tool-call dict outputs.** `_maybe_truncate` may string-truncate stage results; `_carry_output_cap` (2000 chars) is trace-logging-only, not the carried value. Set `output_max_chars: 20000` on the stage and verify (via dry-run or a structural inspection of a real run's `~/.armature/runs/<id>/` output) that the parent receives intact manifest JSON. If tool-call dict outputs are not string-truncated, the cap is harmless.
2. **Carry-forward top-level flattening.** The design relies on the same mechanism that makes `{{ urls_fetched }}` resolve (verified by armature test). The implementer confirms `{{ sources_manifest }}` resolves on iteration 2 via a quick dry-run / structural check before relying on it.
3. **Per-query result shape variance.** Some community tools return `[{query, results:[…]}]`; confirm each (HN/Polymarket/GitHub/Reddit/YouTube) wraps results under a `results` key the walker can traverse, or handle both wrapped and flat shapes in the index builder.

## Out of Scope

- Surfacing `engagement_score` / `source_type` in the renderer (YAGNI — only `engagement_label` is consumed).
- Showing `credibility_note` / `sub_question_index` in the Sources panel (not currently rendered).
- Changing `select_sources` ranking behavior (it already weights engagement; only its *output* is supplemented downstream).
- Re-deriving badges in the parent / bubbling raw tool results out of the subagent.
- Any change to `decide_round.urls_fetched` (kept as-is for dedup).