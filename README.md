# Research-Analyst

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)
[![Built with Armature](https://img.shields.io/badge/built%20with-Armature-00A8E8)](https://github.com/bryansparks/armature)

**Automated research analyst powered by agentic AI.** Given a topic, Research-Analyst searches the web, reads and extracts sources, iteratively deepens coverage, and produces a structured Markdown + HTML research briefing.

> **Example Project:** Research-Analyst is a reference implementation demonstrating [Armature](https://github.com/bryansparks/armature), a YAML-configured agentic workflow harness. Use this repo as a template for building your own Armature-based applications.

```bash
# Install from PyPI
pip install research-analyst

# Run a research task
armature run workflows/research-analyst.yaml --input "topic=AI regulation in the EU"
```

---

## What's New

This release rewrites Research-Analyst as a production-grade **Armature workflow** with an iterative, multi-agent research pipeline:

- **Iterative deep research** — runs 1–3 research rounds, carrying forward gaps, themes, and fetched URLs so each round targets what the previous round missed.
- **Subagent delegation** — the core search/extract/synthesize cycle is isolated in `workflows/research-round.yaml` and invoked in a loop from the parent workflow.
- **Multi-source search** — Tavily web search, Hacker News, Polymarket, and GitHub (free public APIs, no keys needed) plus optional Reddit discussions and YouTube transcripts. Each source degrades gracefully if unreachable.
- **Recency filtering** — optionally constrain a run to a recent window (`recency=30d`, `3d`, `2mo`, `1y`) so queries, hard API filters, and report framing focus on the recent window.
- **Engagement-weighted ranking** — every result carries a native engagement signal (HN points, GitHub stars, Polymarket volume, Reddit score, YouTube views) surfaced as badges in the report and used to weight synthesis.
- **Production reliability** — checkpoint/resume, cross-run source deduplication, continuation for incremental updates, cron/webhook triggers, and strict safety rules.
- **Category-aware reports** — automatically formats output as product reviews, comparisons, how-to guides, fact-checks, or landscape briefings.
- **Self-contained HTML reports** — dark/light theme, table of contents, collapsible sources, and print/export toolbar.

---

## What Research-Analyst Does

A single command runs the full pipeline:

1. **Decomposes** the topic into 5–8 specific sub-questions
2. **Plans** targeted search queries for each sub-question
3. **Searches** the web (Tavily), Hacker News, Polymarket, GitHub, Reddit, and YouTube in parallel
4. **Selects** the most valuable URLs from search results
5. **Fetches** full content from each selected URL in parallel
6. **Extracts** structured findings from each source
7. **Synthesizes** findings into a coherent research summary
8. **Evaluates** coverage completeness — if gaps remain, loops back for another iteration
9. **Writes** a comprehensive research briefing in Markdown
10. **Generates** a self-contained HTML report with dark/light theme, TOC, and collapsible sources

**Typical run time:** 2–5 minutes per iteration (1–3 iterations depending on coverage).

---

## How the Iterative Loop Works

The `deep_research_round` stage in `workflows/research-analyst.yaml` delegates to `workflows/research-round.yaml` as a subagent and runs it in a loop:

```yaml
- id: deep_research_round
  depends_on: [decompose_query]
  subagent_spec: workflows/research-round.yaml
  loop:
    max_iterations: 3
    until: "{{ decide_round.continue_research == false }}"
    carry_forward:
      - decide_round.gaps
      - decide_round.key_themes
      - decide_round.coverage_score
      - decide_round.urls_fetched
      - decide_round.queries_used
      - decide_round.source_count
```

Each research round performs a full `plan → search → select → fetch → extract → synthesize → decide` cycle. The `decide_round` stage returns a `continue_research` boolean plus a list of remaining gaps. If coverage is insufficient and the iteration cap hasn't been reached, Armature carries the selected keys forward and runs another round.

**Why this matters:**

- **Gap-filling queries** — round 2+ generates queries from the gaps identified in round 1, not rephrasings of the original topic.
- **URL deduplication** — `urls_fetched` is carried forward, so later rounds don't waste LLM calls re-reading the same pages.
- **Progressive synthesis** — the evolving report is merged with new findings each round instead of being rewritten from scratch.

For a deep-dive into the design (IterResearch pattern, strategic workspace reconstruction, prompt-injection guards, low-quality filtering), see [`RESEARCH-MECHANICS.md`](./RESEARCH-MECHANICS.md).

---

## Built on Armature

Research-Analyst is a production implementation of [Armature](https://github.com/bryansparks/armature), a YAML-configured agentic workflow harness. The entire research pipeline is declared in workflow specs and executed as a directed acyclic graph (DAG) of LLM agents, tool calls, and subagent delegation.

### Armature Features Used

| Feature | Benefit to Research-Analyst |
|---------|-----------------------------|
| **Iterative loop with `carry_forward`** | Deepens coverage across 1–3 iterations, passing only the compressed state between rounds |
| **Subagent delegation** | Research round runs as an isolated subagent with its own 10-stage pipeline |
| **Fan-out / Fan-in** | Parallel per-query search, per-URL fetch, per-source extraction |
| **Model tier routing** | Cost-optimized routing (small for planning, large for extraction/synthesis) |
| **Cross-run memory** | Remembers which URLs were already fetched across runs |
| **Checkpoint/resume** | Interrupted runs recover gracefully — completed iterations are not re-run |
| **Continuation** | Carries prior research themes forward for incremental "what's new" updates |
| **Cron & webhook triggers** | Scheduled weekly refresh or on-demand webhook-triggered research |
| **Strict safety mode** | Fail-closed tool governance with explicit allow rules |
| **Post-run self-analysis** | Automatic quality review suggests improvements to the workflow |
| **Category-aware formatting** | Report structure adapts to product, comparison, howto, factcheck, or landscape |

### Workflow Specs

| Workflow | Purpose | Stages | Iterations |
|---------|---------|--------|------------|
| `workflows/research-analyst.yaml` | Deep research briefing | 6 (parent) | Up to 3 (subagent loop) |
| `workflows/research-round.yaml` | Single research iteration (subagent) | 10 | 1 per loop iteration |
| `workflows/competitive-intel.yaml` | Competitive intelligence monitor | — | — |

The parent workflow delegates to the subagent in a loop. Each iteration performs a full search→extract→synthesize→evaluate cycle. The loop continues until coverage is adequate or `max_iterations` (3) is reached.

---

## Installation

The recommended way to install is from PyPI:

```bash
pip install research-analyst
```

This pulls in the `armature` runtime and bundled workflow specs automatically.

> **Built on Armature:** Research-Analyst runs on the [Armature](https://github.com/bryansparks/armature) agentic harness. You don't need to install it separately — `pip` resolves it as a dependency, along with the bundled `research-analyst` and `research-round` workflow specs that define the research pipeline.

### From source (for development)

```bash
git clone https://github.com/bryansparks/research-analyst
pip install -e "research-analyst/[dev]"
```

### Optional social sources

To enable Reddit and YouTube research, install the social extras:

```bash
pip install research-analyst[social]
```

This installs `praw` (Reddit) and `youtube-transcript-api` (YouTube transcripts). If these are missing, the workflow continues with web-only results.

Hacker News, Polymarket, and GitHub search are built in and use free public APIs — no extra packages or keys required. An optional `GITHUB_TOKEN` env var raises GitHub rate limits (unauthenticated requests are limited to ~10/min); without it, GitHub search degrades gracefully and the run continues.

### API Keys

Copy `.env.example` to `.env` and add your keys:

```bash
# Required: web search and content extraction
TAVILY_API_KEY=tvly-...

# Required: LLM access (default provider is OpenRouter)
OPENROUTER_API_KEY=sk-or-...

# Optional: Reddit discussion search
# REDDIT_CLIENT_ID=...
# REDDIT_CLIENT_SECRET=...
```

Get a Tavily key at [app.tavily.com](https://app.tavily.com). Get an OpenRouter key at [openrouter.ai](https://openrouter.ai).

---

## Using the Armature Workflow

Research-Analyst is run with the `armature` CLI, not a custom Python entry point. The workflow spec in `workflows/research-analyst.yaml` declares the entire agentic pipeline.

### Basic Research

```bash
armature run workflows/research-analyst.yaml \
  --input "topic=SLM fine-tuning advances using LoRA and distillation"
```

### With Focus Constraint

```bash
armature run workflows/research-analyst.yaml \
  --input "topic=AI regulation" \
  --input "focus=how does the EU AI Act affect open-source LLM providers?"
```

### Recent Results (Recency Window)

Constrain the run to a recent window. Queries are phrased "in the last N days," each source applies its native recency filter (Tavily `days`, PRAW `time_filter`, HN/Polymarket/GitHub date cutoffs), and the report frames findings as recent. Unset or invalid = open-ended (default behavior).

```bash
armature run workflows/research-analyst.yaml \
  --input "topic=GLM-5.2 reception" \
  --input "recency=30d"
```

Supported formats: `Nd` (days), `Nmo` (months, ×30), `Ny` (years, ×365), bare `N` (days). Example values: `3d`, `30d`, `2mo`, `90d`, `1y`.

### Force Fresh Run (Clear Checkpoint)

```bash
armature run workflows/research-analyst.yaml \
  --input "topic=AI regulation" --force
```

### Incremental Research (Continuation)

Research-Analyst remembers prior runs. On subsequent executions with the same topic, it carries forward prior themes and identified gaps:

```bash
# First run: initial research
armature run workflows/research-analyst.yaml --input "topic=AI regulation"

# Second run (one week later): focuses on "what's new"
armature run workflows/research-analyst.yaml --input "topic=AI regulation"
```

### Inputs Reference

| Input | Required | Description |
|-------|----------|-------------|
| `topic` | ✅ | The research question or subject area |
| `focus` | — | Optional angle or constraint, e.g. `focus=regulatory implications` |
| `recency` | — | Recent-results window, e.g. `recency=30d` (`Nd`, `Nmo`, `Ny`, bare `N`). Unset = open-ended |
| `documents` | — | Comma-separated local file paths to include as sources |
| `max_sources` | — | Cap on URLs fetched per round (default: 12) |

### Cron & Webhook Triggers

The workflow spec includes built-in triggers:

```yaml
triggers:
  - type: cron
    schedule: "0 6 * * 1"    # 6am UTC every Monday — weekly topic refresh
  - type: webhook
    path: /webhook/research   # POST with {"topic": "...", "focus": "..."}
```

Configure these in `workflows/research-analyst.yaml` or via your Armature deployment settings.

---

## Output

### HTML Report

Written to `./research-output/<topic>_<run_id>.html`. Features:

- Dark/light theme with aurora gradient hero section
- Table of contents sidebar
- Collapsible source list with credibility ratings
- Category-specific formatting (product reviews, comparisons, how-to guides, etc.)
- Print/export toolbar
- Inline citations linking back to sources

### Report Categories

The `decompose_query` stage classifies the research topic into one of five categories, each with a tailored report format:

| Category | Structure |
|----------|-----------|
| **product** | Executive Summary → Quick Comparison → Detailed Reviews → Verdict |
| **comparison** | At a Glance → By Option → Head-to-Head → Best For |
| **howto** | Prerequisites → Quick Guide → Step-by-Step → Troubleshooting |
| **factcheck** | The Claim → Evidence For → Evidence Against → Verdict |
| **landscape** | Executive Summary → Key Findings → By Sub-Question → Contradictions |

### Markdown Briefing

The validated Markdown report is available in the run output and is used as the source for the HTML render. It includes a source-quality assessment and an appendix of all sources consulted.

---

## Project Structure

```
research-analyst/
├── research/
│   └── tools/
│       ├── web.py              # web_search, fetch_url, read_document, generate_html_report
│       ├── social.py           # search_reddit, search_youtube_videos, fetch_youtube_transcript
│       └── reporting.py        # Visual HTML report generator
├── workflows/
│   ├── research-analyst.yaml  # Parent workflow (6 stages + iterative loop)
│   ├── research-round.yaml    # Subagent workflow (10 stages per iteration)
│   └── competitive-intel.yaml  # Competitive intelligence monitor
├── tests/
│   └── tools/
│       └── test_web.py        # Tool handler unit tests
├── RESEARCH-MECHANICS.md      # Deep-dive into the iterative research design
├── .env.example               # API key template
├── pyproject.toml
└── README.md
```

---

## Configuration

### Model Swapping

Edit `workflows/research-analyst.yaml` to swap LLM providers or models:

```yaml
model_tiers:
  small:
    provider: openrouter
    model: qwen/qwen3.6-27b        # planning, source selection
  medium:
    provider: openrouter
    model: moonshotai/kimi-k2.7    # orchestration, evaluation
  large:
    provider: openrouter
    model: z-ai/glm-5.2            # extraction, synthesis, writing
```

### Iterative Research Loop

The `deep_research_round` stage runs the subagent in a loop:

```yaml
- id: deep_research_round
  subagent_spec: workflows/research-round.yaml
  loop:
    max_iterations: 3
    until: "{{ decide_round.continue_research == false }}"
    carry_forward:
      - decide_round.gaps
      - decide_round.key_themes
      - decide_round.coverage_score
      - decide_round.urls_fetched
      - decide_round.queries_used
      - decide_round.source_count
```

Each iteration receives carry-forward data from the previous one, enabling:
- **Gap-filling queries** on iteration 2+ (focused on identified coverage gaps)
- **URL deduplication** (avoids re-fetching already-read sources)
- **Progressive synthesis** (builds on prior themes rather than rewriting)

### Tuning the Loop

| Setting | Effect |
|---------|--------|
| `max_iterations` | Hard cap on research rounds |
| `carry_forward` | Which state survives between rounds (keep this minimal) |
| `until` | Jinja2 expression that decides when to stop |

For best results, do not carry forward raw search results or full fetched articles — only compressed state like `report`, `gaps`, `urls_fetched`, and `queries_used`.

---

## Extending Research-Analyst

The workflows are standard Armature specs and can be customized:

### Add a New Search Source

Create a tool module in `research/tools/`, then add it to both workflow specs:

```yaml
tools:
  - module: research.tools.web
  - module: research.tools.social
  - module: research.tools.academic   # your new tool
```

### Add a New Stage

```yaml
- id: custom_analysis
  depends_on: [extract_findings]
  role:
    name: Custom Analyst
    type: researcher
    model_tier: large
    description: "Your custom analysis task..."
  output_mode: guided_json
```

### Adjust Safety Rules

The default `safety_mode: strict` blocks any tool not explicitly allowed. Add allow rules for custom tools:

```yaml
safety_rules:
  - tool: my_tool
    condition: {field: query, op: truthy, value: ""}
    action: allow
```

---

## Contributing

Contributions welcome! See [CONTRIBUTING.md](./CONTRIBUTING.md) for guidelines.

## License

MIT. See [LICENSE](./LICENSE) for details.

---

*Research-Analyst is built on [Armature](https://github.com/bryansparks/armature), combining iterative multi-agent research with production-grade workflow orchestration.*
