"""Historique des corrections : un fichier texte par jour, lisible tel quel."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from .config import Config
from .models import ScreenResult

log = logging.getLogger(__name__)


def format_question(question, compare: bool = False) -> str:
    """Une ligne par question, ex. ``1 : ACD   (cours, confiance haute)``."""
    if not question.repondue:
        return f"{question.numero:>3} : ?   (non corrigée)"
    details = [question.source, f"confiance {question.confiance}"]
    if question.cocher_fausses:
        details.append("consigne : cocher les fausses")
    if not question.complete:
        details.append("suite sur l'écran suivant")
    line = f"{question.numero:>3} : {question.texte_court():<8} ({', '.join(details)})"
    if compare and question.cochees is not None and question.cochees != question.lettres:
        line += f"   [tu avais coché : {question.cochees or 'rien'}]"
    return line


def format_result(result: ScreenResult, when: datetime | None = None) -> str:
    when = when or datetime.now()
    total = result.durations.get("total")
    timing = f"{total:.0f} s, " if total is not None else ""
    lines = [f"{when:%H:%M:%S}  ({timing}moteur {result.backend or '?'})"]
    if not result.questions:
        lines.append("    aucune question détectée")
    for question in result.questions:
        lines.append("  " + format_question(question, compare=result.any_ticked))
        if question.note:
            lines.append(f"        {question.note}")
    return "\n".join(lines) + "\n\n"


def append_result(cfg: Config, result: ScreenResult, png: bytes | None = None) -> Path | None:
    """Ajoute un résultat à l'historique du jour. N'échoue jamais : au pire, un avertissement."""
    if not cfg.historique.actif:
        return None
    now = datetime.now()
    try:
        cfg.history_dir.mkdir(parents=True, exist_ok=True)
        path = cfg.history_dir / f"{now:%Y-%m-%d}.txt"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(format_result(result, now))
        if cfg.historique.garder_captures and png:
            captures = cfg.history_dir / "captures"
            captures.mkdir(exist_ok=True)
            (captures / f"{now:%Y-%m-%d_%H-%M-%S}.png").write_bytes(png)
        return path
    except OSError:
        log.warning("Historique non enregistré", exc_info=True)
        return None
