"""
Guard: scripts/install.sh must pre-install the core lazy deps at build time.

The runtime image seals lazy installs (HERMES_DISABLE_LAZY_INSTALLS=1,
issue #8), so anything not baked in by install.sh Step 4 is unavailable at
chat time. ``provider.anthropic`` (anthropic) was missing from that list:
the SDK routes provider=minimax over the anthropic_messages wire
(https://api.minimax.io/anthropic), so the first MiniMax chat in the
container image died with "The 'anthropic' package is required ..." with no
lazy fallback — found while verifying issue #38.
"""
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALL_SH = (REPO_ROOT / "scripts" / "install.sh").read_text(encoding="utf-8")


def test_install_sh_reads_provider_anthropic_from_lazy_deps():
    """Step 4 must pull the anthropic spec from the pinned hermes checkout."""
    assert 'LAZY_DEPS["provider.anthropic"]' in INSTALL_SH, (
        "install.sh Step 4 must pre-install provider.anthropic — the sealed "
        "image (HERMES_DISABLE_LAZY_INSTALLS=1) cannot lazy-install it later, "
        "so provider=minimax / provider=anthropic chats fail with "
        "'The anthropic package is required'"
    )


def test_install_sh_keeps_existing_lazy_deps():
    """The original three pre-installs must not regress while adding anthropic."""
    for feature in ("provider.bedrock", "tts.edge", "image.fal"):
        assert f'LAZY_DEPS["{feature}"]' in INSTALL_SH, feature


def test_install_sh_fallback_includes_anthropic_pin():
    """Fallback literals (used when the hermes checkout is unreadable) stay in sync."""
    # Only the fallback literal (value starts with a package name); the live
    # assignment's value starts with "$(".
    match = re.search(r'_lazy_specs="([a-zA-Z][^"]*)"', INSTALL_SH)
    assert match, "install.sh must keep a fallback _lazy_specs literal"
    specs = match.group(1).split()
    assert any(s.startswith("anthropic==") for s in specs), specs
    assert any(s.startswith("boto3==") for s in specs), specs


def test_install_sh_verifies_anthropic_import():
    """The post-install sanity check must cover anthropic, not only edge-tts."""
    assert 'import edge_tts, anthropic' in INSTALL_SH, (
        "the Step 4 import check must verify anthropic too"
    )
