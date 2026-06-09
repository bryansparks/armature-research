# Research-Analyst

Automated research analyst powered by **Armature**. Given a topic, Research-Analyst searches the web, extracts and reads sources, synthesizes findings, and produces a structured Markdown + HTML research briefing. It runs as a scheduled service, CLI tool, or webhook-triggered workflow.

## Quick Start

### Installation

```bash
pip install research-analyst
```

### Basic Usage

```bash
# Run a research task from the CLI
research-analyst --topic "AI regulation in the EU" --focus "how does the AI Act affect LLM providers?"

# Or trigger via the webhook daemon
# POST http://localhost:8000/webhook/research
# { "topic": "AI regulation in the EU", "focus": "..." }
```

### Incremental Research (Continuation)

Research-Analyst remembers prior runs. On subsequent executions with the same topic, it carries forward prior themes and identified gaps:

```bash
# First run: initial research
research-analyst --topic "AI regulation"

# Second run (one week later): "what's new?"
research-analyst --topic "AI regulation" --incremental

# The system automatically updates the briefing with new sources and themes
```

## Features

### 🔍 Distributed Research
- **Fan-out / Fan-in**: parallel source selection, extraction, and summarization
- **Web search via Tavily**: finds the most relevant sources for your topic
- **Local documents**: combine web research with custom PDFs and Markdown files
- **Cross-run memory**: deduplicates sources across runs — avoid re-reading the same links

### ↻ Incremental Briefings
- **Continuation block**: carries forward prior research themes and coverage gaps
- **Scheduled refresh**: cron trigger for weekly, daily, or hourly updates
- **Webhook trigger**: POST a topic and get back a fresh briefing asynchronously
- **What's new detection**: identifies new themes, risks, or opportunities since the last run

### 📊 Structured Output
- **Markdown briefing**: executive summary, key themes, source index, coverage gaps
- **HTML report**: rendered version suitable for stakeholder distribution
- **JSON metadata**: themes, confidence scores, source evaluation, prior research context

### 🛡️ Production Hardened
- **Safety mode**: strict tool allowlists prevent hallucination or scope creep
- **Timeout & iteration limits**: 40-iteration, 200-LLM-call budget prevents runaway costs
- **Checkpoint & resume**: interruptions don't restart — resume from the last completed stage
- **Model tier routing**: small models for planning (cheap), large for extraction/synthesis (accurate)

## Architecture

Research-Analyst is declared entirely as an Armature YAML workflow (`workflows/research-analyst.yaml`):

```yaml
name: research-analyst
mission: |
  You are a senior research analyst. Given a topic, conduct systematic
  research and synthesize a comprehensive, well-sourced briefing.

model_tiers:
  small: qwen/qwen3.6-27b       # planning
  medium: minimax/minimax-m2.7  # orchestration
  large: moonshotai/kimi-k2.6   # synthesis

triggers:
  - type: cron
    schedule: "0 6 * * 1"       # 6am UTC every Monday
  - type: webhook
    path: /webhook/research

continuation:
  carry_forward:
    - key: synthesize_findings.key_themes
    - key: synthesize_findings.coverage_gaps
  inject_as: prior_research
```

### Stages (High-Level Pipeline)

1. **parse_input** → Validate topic, focus, source constraints
2. **search_sources** → Web search (fan-out for multiple angles)
3. **select_sources** → Rank and filter results; respect max_sources cap
4. **extract_content** → Fetch and parse URLs in parallel
5. **summarize_sources** → Concurrent LLM summaries of each source
6. **synthesize_findings** → Integrate summaries into a cohesive narrative
7. **generate_briefing** → Markdown + HTML export
8. **evaluate_quality** → Judge stage: assess coverage, accuracy, bias

Stages run in parallel where possible. All upstream context flows automatically to downstream stages — no manual wiring.

## Configuration

### Environment Variables

```bash
# Required: one of these LLM provider keys
OPENROUTER_API_KEY=sk-...
# or
ANTHROPIC_API_KEY=sk-ant-...

# Required: web search
TAVILY_API_KEY=tvly-...

# Optional: custom output directory
RESEARCH_OUTPUT_DIR=./reports
```

### Model Swapping

Edit `workflows/research-analyst.yaml` to swap LLM providers or models:

```yaml
model_tiers:
  small:
    provider: anthropic
    model: claude-3-haiku
    api_key_env: ANTHROPIC_API_KEY
```

All tier assignments (`worker: small`, `researcher: large`) remain the same — just change the underlying models.

## CLI Commands

```bash
research-analyst --help

# Run with defaults
research-analyst --topic "Your topic"

# With optional parameters
research-analyst \
  --topic "AI regulation" \
  --focus "how does the EU AI Act affect open-source LLM providers?" \
  --max-sources 15 \
  --output-dir ./reports

# Incremental run (use continuation)
research-analyst \
  --topic "AI regulation" \
  --incremental

# Trigger webhook daemon (listens for POST requests)
research-analyst --daemon --port 8000
```

## Webhook API

```bash
# Start the daemon
research-analyst --daemon --port 8000

# Trigger research asynchronously
curl -X POST http://localhost:8000/webhook/research \
  -H "Content-Type: application/json" \
  -d '{"topic": "AI regulation", "focus": "..."}'

# Response
{
  "run_id": "research-20260529-143022",
  "status": "queued",
  "check_status_at": "http://localhost:8000/runs/research-20260529-143022"
}

# Check status and fetch briefing
curl http://localhost:8000/runs/research-20260529-143022
```

## Showcase: How Armature Powers Research-Analyst

Research-Analyst is a production reference implementation of Armature. It demonstrates:

### **Fan-out / Fan-in** 
- Search stage initiates 3 parallel sub-queries (different angles on the topic)
- Extract stage reads 12 URLs concurrently
- Summarize stage processes summaries in parallel
- All results merge into a single synthesis context

### **Continuation**
- Prior research (themes, gaps, coverage) carries forward to the next run
- Synthesizer sees what was already found and focuses on "what's new"
- Same topic, run weekly — progressive deepening, not repetition

### **Triggers**
- Cron schedule: every Monday at 6am UTC, run a refresh on a fixed topic
- Webhook: POST a topic, get back a briefing asynchronously
- Both flow through the same YAML spec — orchestration is declarative

### **Memory & Deduplication**
- Rolling memory window: last 5 sources are recorded
- Subsequent runs know which URLs were already used — avoids redundant fetches
- Cross-run context prevents the analyst from re-researching the same ground

### **Safety & Governance**
- Tool allowlist: only `web_search`, `fetch_url`, `read_document`, `generate_report` allowed
- Iteration budget: 40-iteration / 200-LLM-call ceiling prevents runaway executions
- Timeout: 1-hour hard deadline for any run

### **Model Tier Routing**
- Small (cheap): planning, search strategy, source filtering
- Large (accurate): extraction, summarization, final synthesis
- Frontier only for the judge stage (final quality evaluation)
- Reduces costs 3-4x vs. running everything on a frontier model

## Contributing

Contributions welcome! Areas of focus:

- New Tavily search strategies (add to `search_sources` stage)
- Additional source types (LinkedIn, academic databases, RSS feeds)
- Document ingestion improvements (add to `extract_content` stage)
- Output formats (add to `generate_briefing` stage)

See [CONTRIBUTING.md](./CONTRIBUTING.md) for guidelines.

## License

MIT. See [LICENSE](./LICENSE) for details.

## Support

- **GitHub Issues**: Report bugs or request features
- **Discussions**: Ask questions about Research-Analyst and Armature
- **Slack**: Join the ElfTech community (coming soon)

---

**Built with [Armature](https://armature.bryansparks.com)** — a YAML-first multi-agent workflow harness that improves itself every run.
