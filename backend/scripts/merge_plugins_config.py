#!/usr/bin/env python3
"""Merge the image seed's plugins.enabled into the runtime config.yaml.

docker-compose bind-mounts ${HOME}/.hermes over the runtime HERMES_HOME and
the entrypoint's seed copy skips top-level entries that already exist, so an
upgrade volume keeps its own config.yaml and never receives the seed's
`plugins.enabled` — the plugins installed at image build (install.sh Step 2c)
would sit on disk disabled. This script appends the seed entries the runtime
config is missing and rewrites the runtime config ONLY when something changed
(idempotent, byte-stable otherwise).

Policy:
  - every name in the seed's `plugins.enabled` that is missing from the
    runtime's `plugins.enabled` is appended (structure created if absent);
  - ALL user entries are preserved (enabled, disabled, and every other key);
  - no change -> the file is not touched at all.

Usage:
    python3 merge_plugins_config.py <seed-config.yaml> <runtime-config.yaml>

Exits 0 on success (including "nothing to do"), 1 on failure.
"""

import sys

try:
    import yaml
except ImportError:  # pragma: no cover - pyyaml is baked into the image
    print("[merge-plugins-config] pyyaml not available", file=sys.stderr)
    sys.exit(1)


def _load(path):
    """Load a YAML mapping; a missing file yields an empty mapping."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
    except FileNotFoundError:
        return {}
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"{path}: top level must be a mapping")
    return data


def _enabled_list(config):
    """The plugins.enabled list of a config (None when structurally absent)."""
    plugins = config.get("plugins")
    if not isinstance(plugins, dict):
        return None
    enabled = plugins.get("enabled")
    if enabled is None:
        return None
    if not isinstance(enabled, list):
        raise ValueError("plugins.enabled must be a list")
    return enabled


def merge(seed_config, runtime_config):
    """Return the names appended (empty list = no change needed)."""
    seed_enabled = _enabled_list(_load(seed_config)) or []
    runtime_enabled = _enabled_list(runtime_config)
    if runtime_enabled is None:
        # Absent or not yet a list: the runtime has no enabled entries yet,
        # so every seed name is missing. Create plugins/enabled around them.
        missing = [name for name in seed_enabled]
        if not missing:
            return []
        plugins = runtime_config.get("plugins")
        if not isinstance(plugins, dict):
            plugins = {}
            runtime_config["plugins"] = plugins
        plugins["enabled"] = list(missing)
        return missing

    missing = [name for name in seed_enabled if name not in runtime_enabled]
    if missing:
        runtime_enabled.extend(missing)
    return missing


def main(argv):
    if len(argv) != 3:
        print(
            "Usage: merge_plugins_config.py <seed-config.yaml> "
            "<runtime-config.yaml>",
            file=sys.stderr,
        )
        return 2

    seed_path, runtime_path = argv[1], argv[2]
    try:
        runtime_config = _load(runtime_path)
        added = merge(seed_path, runtime_config)
        if added:
            with open(runtime_path, "w", encoding="utf-8") as handle:
                yaml.safe_dump(
                    runtime_config,
                    handle,
                    default_flow_style=False,
                    sort_keys=False,
                    allow_unicode=True,
                )
            print(
                "[merge-plugins-config] added to plugins.enabled: "
                + ", ".join(added)
            )
        else:
            print("[merge-plugins-config] plugins.enabled already up to date")
    except Exception as exc:  # noqa: BLE001 - one clear message, rc 1
        print(f"[merge-plugins-config] failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
