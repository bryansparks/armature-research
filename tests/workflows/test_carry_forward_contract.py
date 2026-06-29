"""Contract test for the carry-forward reference pattern the engagement-badge
pipeline depends on.

The workflow's ``collect_source_manifest`` stage reads the prior round's
manifest via ``{{ _iteration.carry_forward.collect_source_manifest.sources_manifest }}``.
A carried dot-path (``collect_source_manifest.sources_manifest``) nests under
``_iteration.carry_forward``; it does NOT flatten to a top-level
``{{ sources_manifest }}``. Only ``carry_forward: None`` (whole-result carry)
flattens to top level — see armature's
``test_loop_carry_forward_top_level_merge``, which covers that case but NOT
dot-path carry.

This test locks the dot-path contract so a future armature change or workflow
edit can't silently re-break cross-round manifest accumulation — the defect the
final whole-branch review caught (the plan had reasoned, incorrectly, that a
bare ``{{ sources_manifest }}`` would resolve).
"""
from armature.spec.models import (
    Stage, HarnessSpec, ModelTiers, ModelTierConfig, ToolCallConfig,
    IterationConfig,
)
from armature.runtime.engine import Harness
from armature.registry.registry import ToolDescriptor
from armature.permissions.permissions import PermissionLevel


def _make_harness(stages, tmp_path) -> Harness:
    spec = HarnessSpec(
        name="wf",
        stages=stages,
        model_tiers=ModelTiers(small=ModelTierConfig(provider="openai", model="gpt-4o-mini")),
    )
    return Harness(spec=spec, session_dir=tmp_path)


def _register(harness, name, fn):
    harness._registry.register(ToolDescriptor(
        name=name, description=name, permission=PermissionLevel.READ_ONLY,
        handler=fn, parameters={},
    ))


async def test_dotpath_carry_forward_resolves_via_iteration_prefix(tmp_path):
    """A carried dot-path is reachable in a tool_call arg as
    {{ _iteration.carry_forward.<stage>.<key> }} on iteration 2+, and a bare
    top-level {{ <key> }} never resolves for dot-path carry."""
    received = []

    async def worker(args):
        received.append({"prior": args.get("prior"), "bare": args.get("bare")})
        # Mimic the subagent result shape: {stage_id: {output_key: value}}.
        return {"collect_source_manifest": {"sources_manifest": [{"url": f"u{len(received)}"}]}}

    harness = _make_harness([Stage(
        id="collect_source_manifest",
        tool_call=ToolCallConfig(name="worker", args={
            "prior": "{{ _iteration.carry_forward.collect_source_manifest.sources_manifest }}",
            "bare": "{{ sources_manifest }}",
        }),
        loop=IterationConfig(
            max_iterations=2,
            carry_forward=["collect_source_manifest.sources_manifest"],
        ),
        depends_on=[],
    )], tmp_path)
    _register(harness, "worker", worker)

    await harness.run({})

    # Iteration 1: no prior result -> carry_forward empty -> both undefined -> None.
    assert received[0]["prior"] is None
    assert received[0]["bare"] is None
    # Iteration 2: dot-path carry nests under _iteration.carry_forward and resolves
    # to the native carried list (NativeEnvironment returns the Python object).
    assert received[1]["prior"] == [{"url": "u1"}]
    # The bare top-level reference never resolves for dot-path carry.
    assert received[1]["bare"] is None