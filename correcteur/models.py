"""Structures de données partagées par tout le projet.

Deux familles :

* les modèles *pydantic* (``Reading``, ``Answers``) décrivent le JSON que le
  modèle doit produire. Leurs noms de champs sont en français parce qu'ils sont
  lus par le modèle en même temps que les consignes ;
* les ``dataclass`` (``ScreenImage``, ``Excerpt``, ``QuestionResult``,
  ``ScreenResult``) circulent à l'intérieur de l'application.

Les schémas restent volontairement simples (tous les champs obligatoires, pas
de valeur optionnelle) pour rester compatibles avec les sorties structurées.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field

Source = Literal["cours", "annale", "connaissances"]
Confidence = Literal["haute", "moyenne", "basse"]
QuestionType = Literal["QRM", "QRU", "QROC", "autre"]


# --------------------------------------------------------------------------
# Phase 1 : lecture de la capture d'écran
# --------------------------------------------------------------------------


# Position donnée aux propositions reprises de la capture précédente : elles ne sont pas sur cet écran.
PREVIOUS_SCREEN = -2


class ReadProposition(BaseModel):
    lettre: str = Field(description="Lettre de la proposition telle qu'affichée : A, B, C...")
    texte: str = Field(description="Texte complet de la proposition, recopié fidèlement.")
    cochee: bool = Field(
        description="true si la case de cette proposition est cochée à l'écran, sinon false."
    )
    x: int = Field(
        description=(
            "Abscisse en pixels, dans l'image, du centre de la case à cocher de la "
            "proposition (ou de sa lettre s'il n'y a pas de case). -1 si inconnue."
        )
    )
    y: int = Field(description="Ordonnée en pixels du même point. -1 si inconnue.")

    @property
    def from_previous_screen(self) -> bool:
        return self.x == PREVIOUS_SCREEN and self.y == PREVIOUS_SCREEN


class ReadQuestion(BaseModel):
    numero: str = Field(
        description=(
            "Numéro de la question tel qu'affiché, en chiffres, par exemple \"12\". "
            "Chaîne vide si le début de la question est au-dessus du bord de l'écran."
        )
    )
    debut_visible: bool = Field(
        description=(
            "false si la question a commencé au-dessus du bord de l'écran : le haut de la capture "
            "n'en montre que la fin (des propositions, parfois la fin de l'énoncé). true sinon."
        )
    )
    type: QuestionType = Field(
        description="QRM (plusieurs réponses possibles), QRU (une seule, aussi appelée QCU), QROC (réponse rédigée) ou autre."
    )
    enonce: str = Field(description="Énoncé de la question, recopié fidèlement (la partie visible).")
    propositions: list[ReadProposition] = Field(
        description="Propositions dont le texte est entièrement visible, dans l'ordre."
    )
    complete: bool = Field(
        description=(
            "true si toutes les propositions de la question sont visibles ; "
            "false si la question continue sous le bord de l'écran."
        )
    )
    requetes: list[str] = Field(
        description=(
            "2 à 4 requêtes de mots-clés (notions, termes médicaux, synonymes) pour "
            "retrouver dans les cours le passage qui traite de cette question."
        )
    )


class Reading(BaseModel):
    questions: list[ReadQuestion]


def is_answerable(question: ReadQuestion) -> bool:
    """Une question se corrige dès qu'au moins une proposition est visible (ou qu'elle est à rédiger)."""
    return bool(question.propositions) or (question.type == "QROC" and question.complete)


# --------------------------------------------------------------------------
# Phase 2 : correction
# --------------------------------------------------------------------------


class Verdict(BaseModel):
    lettre: str
    vraie: bool = Field(
        description="true si l'affirmation est vraie (exacte), false si elle est fausse, quelle que soit la consigne."
    )


class AnsweredQuestion(BaseModel):
    numero: str
    cocher: Literal["vraies", "fausses"] = Field(
        description=(
            "Ce que la consigne demande de cocher : \"fausses\" si elle demande les propositions fausses "
            "ou inexactes, \"vraies\" dans tous les autres cas."
        )
    )
    verdicts: list[Verdict] = Field(
        description="Un verdict par proposition, dans l'ordre. Liste vide pour une question sans propositions."
    )
    reponse_libre: str = Field(
        description="Réponse courte attendue pour une question rédigée (QROC). Chaîne vide pour un QCM."
    )
    source: Source = Field(
        description=(
            "\"cours\" si la réponse s'appuie sur les extraits de cours fournis, \"annale\" si elle "
            "reprend une correction officielle d'annale fournie, \"connaissances\" sinon."
        )
    )
    confiance: Confidence
    note: str = Field(description="Justification en une phrase courte, ou chaîne vide si non demandée.")


class Answers(BaseModel):
    questions: list[AnsweredQuestion]


# --------------------------------------------------------------------------
# Objets internes
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ScreenImage:
    """Image envoyée au modèle (déjà redimensionnée si nécessaire)."""

    png: bytes
    width: int
    height: int


@dataclass(frozen=True)
class Excerpt:
    """Passage de cours retrouvé par la recherche."""

    source: str  # nom du fichier d'origine
    page: int  # numéro de page ou de diapositive (1 = première), 0 si inconnu
    text: str
    score: float = 0.0
    chunk_id: int = -1  # identifiant stable du passage dans l'index


@dataclass
class QuestionResult:
    """Résultat final d'une question, prêt à être affiché."""

    numero: str
    lettres: str = ""  # lettres à cocher, triées, ex. "ACD" ; "" si aucune
    reponse_libre: str = ""
    cochees: str | None = None  # lettres cochées à l'écran ; None si la lecture n'est pas disponible
    complete: bool = True  # False : la question continue sous le bord de l'écran
    repondue: bool = True  # False si la question n'a pas pu être corrigée
    source: str = "connaissances"
    confiance: str = "moyenne"
    note: str = ""
    # lettre -> (x, y) en pixels de l'image envoyée au modèle
    positions: dict[str, tuple[int, int]] = field(default_factory=dict)
    verdicts: dict[str, bool] = field(default_factory=dict)  # lettre -> affirmation vraie ?
    cocher_fausses: bool = False  # la consigne demande de cocher les propositions fausses
    suite: bool = False  # suite d'une question commencée sur la capture précédente

    def texte_court(self) -> str:
        """Ce qui s'affiche à côté du numéro : ``ACD``, une réponse rédigée, « aucune » ou « ? »."""
        if not self.repondue:
            return "?"
        if self.lettres:
            return self.lettres
        if self.reponse_libre:
            return self.reponse_libre
        return "aucune"


def letters_to_tick(verdicts: dict[str, bool], cocher_fausses: bool) -> str:
    """Lettres à cocher : les propositions vraies, ou les fausses si la consigne le demande."""
    return "".join(sorted(letter for letter, true in verdicts.items() if true != cocher_fausses))


@dataclass
class ScreenResult:
    """Résultat complet d'une capture."""

    questions: list[QuestionResult]
    image_width: int = 0
    image_height: int = 0
    backend: str = ""
    durations: dict[str, float] = field(default_factory=dict)  # secondes par étape
    any_ticked: bool = False  # au moins une case cochée a été vue sur la capture

    def resume(self) -> str:
        """Résumé d'une ligne, ex. ``1: ACD  2: C``."""
        return "  ".join(f"{q.numero}: {q.texte_court()}" for q in self.questions)


_NUM_RE = re.compile(r"\d+")


def normalize_number(numero: str) -> str:
    """Ramène ``"Question 12 :"``, ``"12."`` ou ``"QCM 12"`` à ``"12"``.

    Sert à apparier la lecture et la correction d'une même question. S'il n'y a
    aucun chiffre, on renvoie le texte nettoyé en minuscules.
    """
    found = _NUM_RE.findall(numero or "")
    if found:
        return str(int(found[-1]))
    return (numero or "").strip().lower()


def normalize_letter(lettre: str) -> str:
    """``"a."``, ``" B "`` ou ``"c)"`` -> ``"A"``, ``"B"``, ``"C"``."""
    for ch in lettre or "":
        if ch.isalpha():
            return ch.upper()
    return (lettre or "").strip().upper()
