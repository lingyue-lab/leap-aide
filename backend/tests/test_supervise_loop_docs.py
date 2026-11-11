"""
Tests: the supervised development loop is documented (issue #57).

Filesystem-only — the architecture guide has to explain how the loop
runs and how it compares to the workflow-engine approaches, and the
roadmap has to carry the gaps that are not closed yet. A loop whose
design is only visible in the skill body is a loop nobody can maintain.

No server required.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ARCHITECTURE = REPO_ROOT / "docs" / "architecture.md"
ROADMAP = REPO_ROOT / "docs" / "roadmap.md"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_the_architecture_guide_documents_the_loop():
    """The guide has a section for the loop, with its four phases."""
    text = _text(ARCHITECTURE)
    assert "## Supervised development loops" in text, (
        "the architecture guide must document the supervised loop")
    for role in ("planner", "implementer", "tester", "reviewer"):
        assert role in text, f"the loop section must name the {role} role"
    assert "supervise-fix-issue" in text, (
        "the loop section must point at the skill that runs it")
    assert "delegate_task" in text, (
        "the loop section must say how a phase is dispatched")


def test_the_architecture_guide_compares_the_approaches():
    """The comparison names the approaches the loop was chosen against."""
    text = _text(ARCHITECTURE)
    for approach in ("LangGraph", "AutoGen", "GroupChat", "Ralph"):
        assert approach in text, f"the comparison must mention {approach}"


def test_the_architecture_guide_states_the_tradeoff():
    """The three gaps a skill-driven loop gives up are written down."""
    text = _text(ARCHITECTURE)
    for gap in (
        "No enforced DAG",
        "No per-role profile isolation",
        "No cross-session resume",
    ):
        assert gap in text, f"the tradeoff must name the gap: {gap}"


def test_the_decision_is_recorded_as_an_adr():
    """The choice of a skill over a workflow engine is an ADR."""
    text = _text(ARCHITECTURE)
    assert "### ADR-002: The supervised development loop is a skill" in text, (
        "the skill-over-engine decision must be recorded as an ADR")


def test_the_roadmap_carries_the_loop_and_the_open_gaps():
    """The roadmap lists the shipped loop and the SDK-side follow-up."""
    text = _text(ROADMAP)
    assert "supervise-fix-issue" in text, (
        "the roadmap must list the supervised development flow")
    assert "Goal-driven child sessions" in text, (
        "the roadmap must record that a goal cannot drive a child session yet")
