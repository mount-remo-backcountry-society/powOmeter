"""Config validation: the real files pass; typical editing mistakes are caught
with a readable message."""
import shutil
from pathlib import Path

import pytest

from powometer import CONFIG
from powometer.config import as_utc, load, validate


def test_real_config_is_valid():
    assert validate() == []


def test_loads_expected_content():
    c = load()
    assert [m["id"] for m in c.mounts] == ["M1", "M2", "M2b", "M3", "M4", "M5"]
    assert {s["id"] for s in c.sites} == {"shames_top_a", "shames_top_b", "kasiks_high", "kasiks_low",
                                          "shames_bottom"}
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
    ("mounts.yaml", "    from: 2025-12-30T00:00:00Z\n", "    from: 2026-01-02T00:00:00Z\n",
     "72 h gap between M2 and M2b"),
    ("corrections.yaml", "    op: delete\n    station: powometer\n    variable: all\n",
     "    op: offset\n    params: {value: 1}\n    station: powometer\n    variable: all\n", "needs one variable"),
    ("corrections.yaml", "    message: 2025-01-26T19:01:45Z\n", "    message: '2025-01-26T19:01:45'\n",
     "must be a UTC time"),
    ("stations.yaml", "    permission: docs/permissions/moti-2026-10.md\n", "    permission: null\n",
     "written permission"),
    ("stations.yaml", "    permission: docs/permissions/moti-2026-10.md\n",
     "    permission: docs/permissions/missing.md\n", "written permission"),
])
def test_mistakes_are_reported(cfg, name, old, new, expect):
    edit(cfg, name, old, new)
    problems = validate(cfg)
    assert any(expect in p for p in problems), problems


def test_time_strings_need_a_zone():
    """F5: a string without Z would be read as the laptop's local time."""
    assert as_utc("2026-09-26T20:45:00Z").hour == 20
    assert as_utc("2026-09-26T12:45:00-08:00").hour == 20
    with pytest.raises(ValueError, match="no time zone"):
        as_utc("2026-09-26T20:45:00")
    with pytest.raises(ValueError, match="not a time"):
        as_utc("yesterday")


def test_permission_on_file_allows_a_nearby_station(cfg, tmp_path):
    """Rule 3: another owner's station may be on only while its permission
    record is on file."""
    perms = tmp_path / "docs" / "permissions"
    perms.mkdir(parents=True)
    for f in ("moti-2026-10.md", "shames-webcams-2026-10.md"):
        (perms / f).write_text("Permission granted ...", encoding="utf-8")
    assert validate(cfg, tmp_path) == []
    (perms / "moti-2026-10.md").unlink()
    assert any("written permission" in p for p in validate(cfg, tmp_path))


def test_switched_off_stations_need_no_permission(cfg, tmp_path):
    (tmp_path / "docs" / "permissions").mkdir(parents=True)
    (tmp_path / "docs" / "permissions" / "shames-webcams-2026-10.md").write_text("...", encoding="utf-8")
    f = cfg / "stations.yaml"
    s = f.read_text(encoding="utf-8")
    first = s.index("  - id: avcan_56")                  # POW-O-METER itself stays on
    f.write_text(s[:first] + s[first:].replace("    enabled: true\n    publish: true\n",
                                               "    enabled: false\n    publish: false\n"), encoding="utf-8")
    assert validate(cfg, tmp_path) == []
