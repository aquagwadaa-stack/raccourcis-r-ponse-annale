"""Contrat commun aux deux moteurs (API Anthropic et Claude Code en ligne de commande)."""

from __future__ import annotations

from typing import Protocol, Sequence, runtime_checkable

from ..models import Answers, Excerpt, Reading, ScreenImage


class BackendError(Exception):
    """Échec d'un moteur.

    ``str(exc)`` est un message court en français, affichable tel quel dans le
    panneau (ex. « Clé API refusée »). ``detail`` contient l'information
    technique pour le journal ; il ne doit jamais contenir la clé API.
    """

    def __init__(self, message: str, *, detail: str = "") -> None:
        super().__init__(message)
        self.detail = detail


@runtime_checkable
class Backend(Protocol):
    """Un moteur sait lire une capture et corriger des questions."""

    name: str

    def read(self, image: ScreenImage) -> Reading:
        """Phase 1 : transcrit les questions visibles (texte, cases, positions, requêtes)."""
        ...

    def answer(
        self,
        image: ScreenImage,
        reading: Reading | None,
        excerpts: Sequence[Excerpt],
    ) -> Answers:
        """Phase 2 : corrige les questions.

        ``reading`` vaut ``None`` quand la lecture n'est pas disponible : le
        moteur lit alors directement la capture. ``excerpts`` peut être vide.
        """
        ...

    def check(self) -> str:
        """Vérifie que le moteur est utilisable.

        Renvoie une ligne d'état lisible (ex. « API Anthropic : clé présente »)
        ou lève ``BackendError`` avec la marche à suivre.
        """
        ...
