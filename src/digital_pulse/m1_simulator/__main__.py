"""Module entry point: python -m digital_pulse.m1_simulator"""

from __future__ import annotations

import sys

from .cli import main


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
