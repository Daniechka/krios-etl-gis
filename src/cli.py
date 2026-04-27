"""KRIOS command-line entry point (installed as the `krios` command)."""

import sys

from src.collectors.run_all import _parse_args, main as _collect


def main() -> None:
    args = _parse_args()
    _, errors = _collect(enabled=args.collectors)
    sys.exit(len(errors))


if __name__ == "__main__":
    main()
