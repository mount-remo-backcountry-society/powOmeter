"""Config validation: the real files pass; typical editing mistakes are caught
with a readable message."""
import shutil
from pathlib import Path

import pytest

from powometer import CONFIG
from powometer.config import load, validate


def test_real_config_is_valid():
    assert validate() == []


def test_loads_expected_content():
    c = load()
    assert [m["id"] for m in c.mounts] == ["M1", "M2", "M2b", "M3", "M4", "M5"]
    assert {s["id"] for s in c.sites} == {"shames_top_a", "shames_top_b"}
    assert c.no_echo_cm == [498, 499, 500]


@pytest.fixture
def cfg(tmp_path):
    d = tmp_path / "config"
    shutil.copytree(CONFIG, d)
    return d


def edit(d: Path, name: str, old: str, new: str):
    f = d / name
    s = f.read_text(encoding="utf-8")
    assert old in s
    f.write_text(s.replace(old, new, 1), encoding="utf-8")


@pytest.mark.parametrize("name,old,new,expect", [
    ("mounts.yaml", "until: 2025-09-28T18:03:32Z", "until: 2025-10-28T18:03:32Z", "overlaps"),
    ("mounts.yaml", "reference_m: 4.51", "reference_m: 451", "reference_m must be a distance"),
    ("mounts.yaml", "method: bare_ground", "method: guess", "method must be one of"),
    ("corrections.yaml", "    op: delete", "    op: remove", "unknown op 'remove'"),
    ("corrections.yaml", "    by: JK\n", "    by: Julian Krick\n", "'by' must be initials"),
    ("corrections.yaml", "    message: 2025-01-26T19:01:45Z\n", "", "give either 'message'"),
    ("field_visits.yaml", "who: JK", "who: julian", "'who' must be initials"),
    ("sites.yaml", "until: 2026-09-26T18:15:00Z", "until: 2024-09-26T18:15:00Z", "'until'"),
    ("stations.yaml", "type: powometer", "type: davis", "unknown type 'davis'"),
    ("mounts.yaml", "  - id: M1\n", "  - id: M1\n   bad_indent: [\n", "not valid YAML"),
])
def test_mistakes_are_reported(cfg, name, old, new, expect):
    edit(cfg, name, old, new)
    problems = validate(cfg)
    assert any(expect in p for p in problems), problems
