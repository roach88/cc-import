"""Sanity smoke: the plugin's manifest is well-formed and contains expected metadata.

This is the only test in slice 1 Unit 1 — its purpose is to keep ``pytest`` from
exiting with code 5 ("no tests collected") on an otherwise-empty repo, and to
catch typos in ``plugin.yaml`` early. Real behavioral tests start in Unit 2 with
the converter's pure helpers.
"""

from __future__ import annotations

from pathlib import Path

import yaml


def test_plugin_yaml_is_well_formed() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    manifest = yaml.safe_load((repo_root / "plugin.yaml").read_text())

    assert manifest["name"] == "cc-import"
    assert manifest["version"] == "0.1.0"
    assert manifest["author"] == "Tyler Barstow"
    assert "description" in manifest
    assert isinstance(manifest["description"], str)
    assert len(manifest["description"]) > 0
