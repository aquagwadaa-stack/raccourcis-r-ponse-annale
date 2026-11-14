"""Journal de l'application : fichier tournant, et console quand il y en a une."""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_FILENAME = "correcteur.log"


class _HideApiKey(logging.Filter):
    """Remplace la clé API par des étoiles si elle se glisse dans un message."""

    def filter(self, record: logging.LogRecord) -> bool:
        key = os.environ.get("ANTHROPIC_API_KEY", "")
        if len(key) >= 8:
            message = record.getMessage()
            if key in message:
                record.msg = message.replace(key, "***")
                record.args = None
        return True


def setup_logging(log_dir: Path | None, *, console: bool = True, level: int = logging.INFO) -> Path | None:
    """Configure le journal ; renvoie le chemin du fichier, ou ``None`` s'il n'a pas pu être créé."""
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    root.setLevel(level)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s : %(message)s", "%Y-%m-%d %H:%M:%S")
    path: Path | None = None
    if log_dir is not None:
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            path = log_dir / LOG_FILENAME
            handler: logging.Handler = RotatingFileHandler(path, maxBytes=1_000_000, backupCount=2, encoding="utf-8")
            handler.setFormatter(formatter)
            handler.addFilter(_HideApiKey())
            root.addHandler(handler)
        except OSError:
            path = None
    # Lancé sans console (pythonw), sys.stderr vaut None : pas de sortie console.
    if console and sys.stderr is not None:
        stream = logging.StreamHandler()
        stream.setFormatter(formatter)
        stream.addFilter(_HideApiKey())
        root.addHandler(stream)
    logging.getLogger("httpx2").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    return path
