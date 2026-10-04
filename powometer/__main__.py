"""Command line:  python -m powometer <command>

  validate   check the files in config/ and print any problems
  build      rebuild everything from raw/ and config/ into out/
"""
import sys


def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else ""
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
