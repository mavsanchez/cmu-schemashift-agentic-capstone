"""Small logging configuration shared by command-line entry points."""

from __future__ import annotations

import logging


def configure_logging(level: int | str = logging.INFO) -> None:
    """Configure predictable local console logging without external sinks."""

    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
