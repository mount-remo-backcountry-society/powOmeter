"""Command line:  python -m powometer <command>

  validate   check the files in config/ and print any problems
  build      rebuild everything from raw/ and config/ into out/
  approve    freeze a reviewed period:
             approve <variable> <from UTC> <until UTC> <snapshot name> <initials>
             e.g. approve snow_depth 2025-01-26T18:58:34Z 2025-09-28T18:03:32Z 2025-season JK
"""
import sys


def approve(argv: list[str]) -> int:
    if len(argv) != 7:
        print(__doc__)
        return 2
    from datetime import date

    import yaml

    from . import APPROVED, ROOT, approval, assemble, corrections, qc, radio
    from .config import load
    _, _, variable, start, end, name, by = argv
    cfg = load()
    msgs = radio.load_all()
    obs = assemble.merge(assemble.sd_points(cfg, msgs), assemble.radio_points(cfg, msgs))
    obs = corrections.apply(qc.run(obs, cfg), cfg)
    path = APPROVED / "powometer" / name / f"{variable}.csv"
    sha = approval.write_snapshot(obs, "powometer", variable, start, end, path)
    entry = [{"station": "powometer", "variable": variable, "from": start, "until": end,
              "snapshot": path.relative_to(ROOT).as_posix(), "sha256": sha,
              "by": by, "date": date.today().isoformat()}]
    print(f"Snapshot written: {path.relative_to(ROOT).as_posix()}")
    print("Add this to config/approvals.yaml under 'approvals:' and commit both files:\n")
    print(yaml.safe_dump(entry, sort_keys=False))
    return 0


def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd == "approve":
        return approve(argv)
    if cmd == "validate":
        from .config import validate
        problems = validate()
        if not problems:
            print("Config OK: no problems found.")
            return 0
        print(f"{len(problems)} problem(s) in config/:")
        for p in problems:
            print("  - " + p)
        return 1
    if cmd == "build":
        from .build import build
        return build()
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
