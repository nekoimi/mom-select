"""ETF CLI entry point."""

from mom_select.cli import build_parser, main, run

__all__ = ["build_parser", "main", "run"]


if __name__ == "__main__":
    main()

