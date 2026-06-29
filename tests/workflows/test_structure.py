"""Structural tests: research-round.yaml and research-analyst.yaml wiring for
recency + community sources."""
import pathlib
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
ROUND = ROOT / "workflows" / "research-round.yaml"
ANALYST = ROOT / "workflows" / "research-analyst.yaml"


def _load(path):
    return yaml.safe_load(path.read_text())


# ── research-round.yaml ────────────────────────────────────────────────────────

def test_round_registers_community_and_recency_modules():
    spec = _load(ROUND)
    modules = [t["module"] for t in spec["tools"]]
    assert "research.tools.communities" in modules
    assert "research.tools.recency" in modules


def test_round_declares_recency_input():
    names = [i["name"] for i in _load(ROUND)["contracts"]["inputs"]]
    assert "recency" in names


def test_round_has_prepare_recency_and_new_search_stages():
    ids = [s["id"] for s in _load(ROUND)["stages"]]
    for sid in ("prepare_recency", "run_hn_search", "run_polymarket_search", "run_github_search"):
        assert sid in ids, f"missing stage {sid}"


def test_select_sources_depends_on_new_stages():
    spec = _load(ROUND)
    stage = next(s for s in spec["stages"] if s["id"] == "select_sources")
    deps = stage["depends_on"]
    for sid in ("run_hn_search", "run_polymarket_search", "run_github_search"):
        assert sid in deps, f"select_sources missing dep {sid}"


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


# ── research-analyst.yaml ──────────────────────────────────────────────────────

def test_analyst_declares_recency_input():
    names = [i["name"] for i in _load(ANALYST)["contracts"]["inputs"]]
    assert "recency" in names
