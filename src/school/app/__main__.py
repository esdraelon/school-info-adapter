"""``python -m school.app <serve|scrape|health>`` entry shim."""

from school.app.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
