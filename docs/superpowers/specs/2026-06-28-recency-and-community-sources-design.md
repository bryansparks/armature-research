# Recency Option + Community Sources — Design

**Date:** 2026-06-28
**Status:** Approved (pending user spec review)
**Inspiration:** the `last30days` Claude Code skill (idea-only — no code reused)

## Goal

Add two capabilities to the Research-Analyst workflow, plus one quality improvement:

1. **Recency option** — constrain a research run to a recent window (e.g. last 30 days, last 3 days, last 2 months) via a CLI input.
2. **Community sources** — check the same kinds of sites `last30days` searches, intrinsically: add Hacker News, Polymarket, and GitHub alongside the existing Web/Reddit/YouTube.
3. **Engagement-weighted ranking** — surface native engagement metrics on every result and have the synthesis stage weight high-engagement items more heavily ("ranked by real signals, not editors").

## Non-goals (out of scope)

- X/Twitter, TikTok, Instagram, Threads, Bluesky, Pinterest, Perplexity (require paid scraping services, cookies, or paid keys — contradicts the "free public APIs only" decision).
- Any code from the `last30days` repo. The skill is inspiration for the *idea* and the engagement-ranking philosophy only.
- Wiring the new sources/stages into `workflows/competitive-intel.yaml`. Deferred to a follow-on; this spec covers `research-analyst.yaml` + `research-round.yaml` only.

## Background / current state

- The workflow is run with the `armature` CLI (separate package, not in this repo):
  `armature run workflows/research-analyst.yaml --input "topic=…"`.
  Inputs are declared in a workflow's `contracts.inputs` and passed via repeatable `--input key=value`.
- `research-analyst.yaml` declares inputs and delegates the research loop to `research-round.yaml` as a subagent (`deep_research_round` stage).
- `research-round.yaml` is where search fan-out lives: `run_searches` (web_search) → `run_reddit_search` (search_reddit) → `run_youtube_search` (search_youtube_videos) → `fetch_youtube_transcripts` → `select_sources` → `fetch_articles` → `extract_findings` → `synthesize_round` → `decide_round`.
- Tool modules under `research/tools/` register handlers via a `register(registry)` function using `ToolDescriptor` (name, description, permission, handler, parameters schema). Each handler is `async def _handle_x(args) -> dict` returning `{…, results: [...], error?}` and degrades gracefully when credentials/packages are absent.
- Existing sources: `research/tools/web.py` (web_search, fetch_url, read_document, generate_html_report via Tavily) and `research/tools/social.py` (search_reddit via PRAW, search_youtube_videos via Tavily `site:youtube.com`, fetch_youtube_transcript via youtube-transcript-api).
- Recency hooks that already exist: Tavily `client.search(days=…)`; PRAW `search(time_filter=…)`; the workflow already injects `_date_context` for date grounding.

## Design

### Section 1 — Recency option

**Input contract.** Add `recency` to `contracts.inputs` in `workflows/research-analyst.yaml`:

```yaml
- name: recency   # Optional: recent-results window, e.g. "30d", "3d", "2mo", "90d", "1y". Unset = open-ended.
```

Thread `recency` into the `deep_research_round` subagent invocation, and add it to `research-round.yaml`'s own `contracts.inputs`.

**CLI usage:**

```
armature run workflows/research-analyst.yaml \
  --input "topic=GLM-5.2 reception" --input "recency=30d"
```

Supported formats: `Nd` (days), `Nmo` (months, ×30), `Ny` (years, ×365), bare `N` (days). Invalid input is treated as **unset** with a logged warning — never a hard failure.

**Parser — `research/tools/recency.py`:**

```python
def parse_recency(s: str | None) -> dict | None:
    """Return {days: int, phrase: str, iso_start: str} or None if unset/invalid.

    phrase:  "in the last 30 days"  (empty string when unset)
    iso_start: UTC ISO-8601 cutoff timestamp (e.g. "2026-05-29T09:48:00Z")
    """
```

Pure function, no I/O — fully unit-testable. `iso_start` is computed from the caller-provided "now" (passed in as an argument, not `datetime.now()`, so tests are deterministic).

**Hard filters (per tool, only when `days` is present):**

| Tool | Mechanism |
|------|-----------|
| `web_search`, `search_youtube_videos` | Tavily `days=N`. Graceful if the SDK/plan ignores it. |
| `search_reddit` | PRAW `time_filter` mapped to nearest bucket: `day` (≤1d), `week` (≤7d), `month` (≤31d), `year` (≤366d). |
| `search_hackernews` | Algolia `numericFilters=created_at_i>{cutoff_epoch}`. |
| `search_polymarket` | Filter markets whose `startDate`/endDate falls within the window. |
| `search_github` | GitHub search qualifier `created:>{iso_date}` (repos) / `pushed:>{iso_date}`. |

Each tool accepts an optional `recency_days` (int) and/or `recency_iso` (str) arg in its schema. When absent, behavior is unchanged.

**Exposing `parse_recency` as a tool + `prepare_recency` stage.** The workflow can't call the Python parser directly from Jinja, so `recency.py` also `register()`s a `parse_recency` tool (pure, no network). `research-round.yaml` gains a first stage `prepare_recency` that calls it:

```yaml
- id: prepare_recency
  tool_call:
    name: parse_recency
    args:
      recency: "{{ recency }}"     # raw input, e.g. "30d"; "" when unset
      now: "{{ _date_context }}"   # injected date string for determinism
```

It returns `{days, phrase, iso_start}` (all empty/zero when unset). Downstream stages reference it via Jinja — e.g. `recency_days: "{{ prepare_recency.days }}"`, and prompts use `{{ prepare_recency.phrase }}`. This mirrors the existing pattern where stages reference prior stage outputs.

**Soft phrasing (workflow layer):** `{{ prepare_recency.phrase }}` (default empty string) is interpolated into prompts:
- `plan_round_queries` appends "in the last 30 days" to generated search queries.
- `synthesize_round` and `write_report` prompts note the recency constraint so the report frames findings as recent.

### Section 2 — Three new sources

**New module `research/tools/communities.py`** with three async handlers + `register(registry)`, following the exact `social.py` pattern (graceful degradation, `ToolDescriptor`, schema, returns `{query, results: [...], error?}`).

**`search_hackernews(query, recency_days?, max_results=5)`**
- Endpoint: `https://hn.algolia.com/api/v1/search_by_date?query=…&numericFilters=created_at_i>{cutoff}&hitsPerPage={max_results}` (no API key).
- Result fields: `url` (HN post URL), `title`, `points`, `num_comments`, `author`, `created_at`, `engagement_score`.

**`search_polymarket(query, recency_days?, max_results=5)`**
- Endpoint: `https://gamma-api.polymarket.com/markets?_limit=…&active=true&query=…` (no API key); client-side filter to the recency window on `startDate`/endDate.
- Result fields: `slug`, `question`, `url`, `volume`, `liquidity`, `outcomes` (with odds), `endDate`, `engagement_score`.

**`search_github(query, recency_days?, max_results=5)`**
- Endpoint: `https://api.github.com/search/repositories?q={query}+{recency_qualifier}&per_page=…`.
- Optional `GITHUB_TOKEN` env var raises rate limits (unauthenticated = 10 req/min). Degrades gracefully without it — returns an `error` result, workflow continues.
- Result fields: `full_name`, `url`, `description`, `stargazers_count`, `language`, `updated_at`, `engagement_score`.

**Wiring in `research-round.yaml`:**

1. Add `- module: research.tools.communities` to the `tools:` block.
2. Add three `safety_rules` entries (allow when `query` truthy), mirroring the Reddit/YouTube rules.
3. Add three new fan-out stages alongside `run_searches`/`run_reddit_search`/`run_youtube_search`:
   - `run_hn_search` → `search_hackernews`
   - `run_polymarket_search` → `search_polymarket`
   - `run_github_search` → `search_github`
4. Add all three to `select_sources.depends_on` (parallel fan-out; `select_sources` waits for all searches).
5. Add HN/Polymarket/Gitbank context blocks to `select_sources` and `synthesize_round` prompt inputs, parallel to the existing Reddit/YouTube blocks — telling the synthesizer what each source represents (HN = developer sentiment; Polymarket = crowd/money-backed forecasts; GitHub = code/projects & traction by stars).

**`competitive-intel.yaml`:** deferred (follow-on). The same `communities.py` module and `safety_rules` pattern will drop in there later.

### Section 3 — Engagement-weighted ranking

**Surface native metrics** on every result. Keep the source-native field (`points`, `num_comments`, `stargazers_count`, `volume`, `score`, `view_count`) **and** add a normalized `engagement_score` in `[0, 1]` via a small normalizer so the synthesizer sees a comparable number across heterogeneous sources.

- Normalizer lives in `research/tools/communities.py` (and a counterpart added to `social.py` for Reddit/YouTube). It uses simple source-specific scaling (e.g. log-scaled stars, log-scaled upvotes) — deliberately crude and transparent, not a learned model.
- `web_search`'s existing Tavily `score` is passed through as its engagement signal.

**Weight in synthesis:**
- `select_sources` prompt updated to prefer high-engagement items: "rank by real signals (upvotes, stars, volume, views), not editor placement."
- `synthesize_round` prompt updated to weight high-engagement sources more heavily and to cite the metric when drawing on a source.
- `select_sources` output schema gains an optional `engagement` field per selected source.

**Report rendering (`research/tools/reporting.py`):**
- Lightweight per-source-type badges in the existing per-result render path:
  - HN: `▲ 1.2k · 340 comments`
  - GitHub: `★ 4.5k`
  - Polymarket: `$1.2M vol · 68% Yes`
  - Reddit: `▲ {score}`
  - YouTube: `▶ {views}`
  - Web: Tavily score badge (existing behavior preserved)
- Reuses the existing per-result rendering path; no structural report changes.

## Architecture / data flow

```
armature CLI
  --input "topic=…" --input "recency=30d"
        │
        ▼
research-analyst.yaml  (contracts.inputs: + recency)
  └─ deep_research_round  ── passes recency down ──┐
                                                   ▼
research-round.yaml  (contracts.inputs: + recency)
  ├─ prepare_recency [NEW]   parse_recency tool → {days, phrase, iso_start}
  ├─ plan_round_queries      uses {{ prepare_recency.phrase }}
  ├─ run_searches            web_search(recency_days={{ prepare_recency.days }})
  ├─ run_reddit_search       search_reddit(recency_days)
  ├─ run_youtube_search      search_youtube_videos(recency_days)
  ├─ run_hn_search    [NEW]  search_hackernews(recency_days/iso)
  ├─ run_polymarket_search [NEW] search_polymarket(recency_days/iso)
  ├─ run_github_search [NEW] search_github(recency_days/iso)
  ├─ select_sources          depends_on: all six searches; engagement-weighted
  ├─ fetch_articles
  ├─ extract_findings
  ├─ synthesize_round        engagement-weighted; notes recency
  └─ decide_round
        │
        ▼
write_report / generate_html  (reporting.py: engagement badges)
```

New/changed units:
- `research/tools/recency.py` — NEW. Pure `parse_recency` parser + `register(registry)` exposing it as a `parse_recency` tool. One purpose, zero deps.
- `research/tools/communities.py` — NEW. HN/Polymarket/GitHub handlers + normalizer. One purpose: community-signal sources.
- `research/tools/social.py` — MODIFIED. Add `recency_days` → `time_filter` mapping to `search_reddit`; pass `days` to Tavily in `search_youtube_videos`; add `engagement_score` normalizer for Reddit/YouTube results.
- `research/tools/web.py` — MODIFIED. `web_search` accepts `recency_days` and passes `days` to Tavily.
- `research/tools/reporting.py` — MODIFIED. Engagement badges per source type.
- `workflows/research-analyst.yaml` — MODIFIED. `+ recency` input; thread into `deep_research_round`.
- `workflows/research-round.yaml` — MODIFIED. `+ recency` input; `+ communities` module; three new stages; `select_sources.depends_on` updated; new prompt context blocks; `{{ recency_phrase }}` in prompts.

## Error handling

- Invalid `recency` string → treated as unset, log a warning, run open-ended. Never abort the run.
- Network failure / 429 / missing optional package (`GITHUB_TOKEN` absent, Polymarket/HN unreachable) → tool returns `{query, results: [], error: str(exc)}`; workflow continues with the other sources. This is the existing graceful-degradation contract — the new tools follow it exactly.
- Recency filter unsupported by a given API (e.g. Tavily plan ignores `days`) → results may be broader than requested; the soft prompt phrasing still biases toward recent content. Acceptable; no abort.

## Testing

- **`parse_recency` unit tests** — every supported unit (`3d`, `30d`, `90d`, `2mo`, `1y`, bare `N`), invalid input → `None`, `None`/empty → `None`, deterministic `iso_start` from an injected "now".
- **Per-tool tests with mocked HTTP** (stubbed Algolia / gamma / GitHub responses):
  - Returns parsed results with expected fields.
  - Recency filter is applied (correct query params / qualifiers).
  - `engagement_score` is present and in `[0, 1]`.
- **Degradation tests** — no network / no `GITHUB_TOKEN` → `{results: [], error: …}`, no exception.
- **Pass-through tests** — `web_search`/`search_reddit` receive `recency_days` and forward the correct param to Tavily/PRAW.
- **Structural YAML test** (alongside existing structural tests) — `research-round.yaml` registers `research.tools.communities` and `research.tools.recency`, declares `recency` input, has `prepare_recency` + the three new search stages, and `select_sources.depends_on` includes the new stages; `research-analyst.yaml` declares `recency` input and threads it into `deep_research_round`.

## Risks / trade-offs

- **`engagement_score` normalization is crude.** Source-specific log scaling is a heuristic, not a calibrated model. Trade-off: it gives the synthesizer a comparable signal across HN/GitHub/Polymarket/Reddit/YouTube without a learned ranker. Deemed worth it for the cross-source weighting the user wants; can be refined later.
- **Polymarket relevance** is narrow (prediction markets). It will often return no relevant markets for a topic. Graceful degradation handles this; the synthesizer is told it's optional signal.
- **GitHub rate limits** (unauthenticated) are low. `GITHUB_TOKEN` is optional; without it, the tool degrades. Documented in README.
- **Tavily `days` honoring** varies by plan/SDK version. Soft phrasing is the backstop.