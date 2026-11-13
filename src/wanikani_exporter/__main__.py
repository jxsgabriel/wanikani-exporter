"""Allow running the package directly via `python3 -m wanikani_exporter`."""

from wanikani_exporter.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
