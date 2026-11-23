"""Enchaînement complet pour une capture : lecture -> recherche dans les cours -> correction.

Ce module ne dépend ni de l'interface ni d'un moteur précis : il reçoit un
``Backend`` et, s'il existe, un index de cours.
"""

from __future__ import annotations

import logging
import time
from typing import Callable, Protocol, Sequence

from .ai.base import Backend, BackendError
from .models import (
    PREVIOUS_SCREEN,
    AnsweredQuestion,
    Answers,
    Excerpt,
    QuestionResult,
    Reading,
    ReadQuestion,
    ScreenImage,
    ScreenResult,
    is_answerable,
    letters_to_tick,
    normalize_letter,
    normalize_number,
)

log = logging.getLogger(__name__)

# Étapes signalées à l'interface
STAGE_READ = "lecture"
STAGE_SEARCH = "recherche"
STAGE_ANSWER = "correction"

RESULTS_PER_QUERY = 5
RRF_K = 10  # constante de la fusion par rangs réciproques
MAX_EXCERPT_CHARS = 60_000  # plafond du texte de cours envoyé pour une capture

ProgressFn = Callable[[str], None]


class Searcher(Protocol):
    """Ce que le pipeline attend de l'index de cours."""

    def search(self, query: str, limit: int = 8) -> list[Excerpt]: ...

    def is_empty(self) -> bool: ...


def _excerpt_key(ex: Excerpt) -> tuple:
    if ex.chunk_id >= 0:
        return ("id", ex.chunk_id)
    return ("txt", ex.source, ex.page, hash(ex.text))


def _question_queries(q: ReadQuestion) -> tuple[list[str], list[str]]:
    """Renvoie (requêtes générales, requêtes par proposition) pour une question."""
    stem = q.enonce.strip()
    general = [r.strip() for r in q.requetes if r and r.strip()]
    if stem:
        general.append(stem)
    per_prop = [f"{stem} {p.texte.strip()}".strip() for p in q.propositions if p.texte.strip()]
    return general, per_prop


def retrieve_for_question(q: ReadQuestion, searcher: Searcher, per_question: int) -> list[Excerpt]:
    """Passages les plus utiles pour une question.

    Chaque proposition garde son meilleur passage (pour que chacune ait une
    chance d'être tranchée par le cours), puis on complète avec les passages
    les mieux classés toutes requêtes confondues.
    """
    if per_question <= 0:
        return []
    general, per_prop = _question_queries(q)
    fused: dict[tuple, float] = {}
    by_key: dict[tuple, Excerpt] = {}
    best_per_prop: list[tuple] = []

    def run(query: str) -> list[Excerpt]:
        try:
            return searcher.search(query, limit=RESULTS_PER_QUERY)
        except Exception:  # une requête ratée ne doit pas faire échouer la correction
            log.exception("Recherche impossible pour la requête %r", query[:80])
            return []

    for query in general:
        for rank, ex in enumerate(run(query)):
            key = _excerpt_key(ex)
            by_key.setdefault(key, ex)
            fused[key] = fused.get(key, 0.0) + 1.0 / (RRF_K + rank)
    for query in per_prop:
        results = run(query)
        for rank, ex in enumerate(results):
            key = _excerpt_key(ex)
            by_key.setdefault(key, ex)
            fused[key] = fused.get(key, 0.0) + 1.0 / (RRF_K + rank)
        if results:
            best_per_prop.append(_excerpt_key(results[0]))

    ordered: list[tuple] = []
    for key in best_per_prop:
        if key not in ordered:
            ordered.append(key)
    for key, _score in sorted(fused.items(), key=lambda kv: kv[1], reverse=True):
        if key not in ordered:
            ordered.append(key)
    return [by_key[key] for key in ordered[:per_question]]


def retrieve(
    reading: Reading,
    searcher: Searcher,
    per_question: int,
    max_chars: int = MAX_EXCERPT_CHARS,
) -> list[Excerpt]:
    """Passages de cours pour toutes les questions à corriger d'une capture, sans doublon."""
    per_q = [
        retrieve_for_question(q, searcher, per_question) for q in reading.questions if is_answerable(q)
    ]
    selected: list[Excerpt] = []
    seen: set[tuple] = set()
    total = 0
    # On sert les questions à tour de rôle pour qu'aucune ne soit privée de
    # passages si le plafond de taille est atteint.
    for rank in range(max((len(lst) for lst in per_q), default=0)):
        for lst in per_q:
            if rank >= len(lst):
                continue
            ex = lst[rank]
            key = _excerpt_key(ex)
            if key in seen:
                continue
            if total + len(ex.text) > max_chars and selected:
                continue
            seen.add(key)
            selected.append(ex)
            total += len(ex.text)
    return selected


def _valid_position(x: int, y: int, image: ScreenImage) -> bool:
    return 0 <= x < image.width and 0 <= y < image.height


def continue_previous(reading: Reading, previous: Reading | None) -> Reading:
    """Raccroche le haut de la capture à la question laissée inachevée sur la capture précédente.

    Quand l'énoncé est trop long pour tenir à l'écran, l'étudiant capture une
    question en plusieurs fois : le haut de la nouvelle capture montre alors des
    propositions sans numéro ni énoncé. On leur rend le numéro, l'énoncé et les
    premières propositions de la capture précédente, pour que la correction ait
    tout le contexte (les propositions reprises sont marquées ``PREVIOUS_SCREEN``).
    """
    if not reading.questions or reading.questions[0].debut_visible:
        return reading
    first = reading.questions[0]
    earlier = previous.questions[-1] if previous is not None and previous.questions else None
    if earlier is None:
        return reading
    visible = {normalize_letter(p.lettre) for p in first.propositions}
    carried = [
        p.model_copy(update={"x": PREVIOUS_SCREEN, "y": PREVIOUS_SCREEN, "cochee": False})
        for p in earlier.propositions
        if normalize_letter(p.lettre) not in visible
    ]
    stem = earlier.enonce.strip()
    tail_of_stem = first.enonce.strip()
    if tail_of_stem and tail_of_stem not in stem:
        stem = f"{stem} […] {tail_of_stem}" if stem else tail_of_stem
    joined = first.model_copy(
        update={
            "numero": first.numero or earlier.numero,
            "type": earlier.type if first.type == "autre" else first.type,
            "enonce": stem,
            "propositions": carried + list(first.propositions),
            "requetes": list(dict.fromkeys([*earlier.requetes, *first.requetes])),
        }
    )
    return Reading(questions=[joined, *reading.questions[1:]])


def _result_from_answer(a: AnsweredQuestion, known_letters: list[str] | None = None) -> QuestionResult:
    verdicts = {normalize_letter(v.lettre): v.vraie for v in a.verdicts}
    if known_letters:
        verdicts = {letter: true for letter, true in verdicts.items() if letter in known_letters}
    fausses = a.cocher == "fausses"
    return QuestionResult(
        numero=normalize_number(a.numero) or a.numero.strip() or "?",
        lettres=letters_to_tick(verdicts, fausses),
        reponse_libre=a.reponse_libre.strip(),
        cochees=None,
        source=a.source,
        confiance=a.confiance,
        note=a.note.strip(),
        verdicts=verdicts,
        cocher_fausses=fausses,
    )


def merge(reading: Reading | None, answers: Answers | None, image: ScreenImage) -> ScreenResult:
    """Réunit lecture et correction en un résultat affichable.

    Seules les questions dont au moins une proposition est visible apparaissent :
    un énoncé seul, coupé en bas de l'écran, sera corrigé sur la capture suivante.
    """
    answered = list(answers.questions) if answers else []

    if reading is None:
        return ScreenResult(
            questions=[_result_from_answer(a) for a in answered],
            image_width=image.width,
            image_height=image.height,
        )

    targets = [q for q in reading.questions if is_answerable(q)]
    by_number = {normalize_number(a.numero): a for a in answered}
    # Si les numéros ne se correspondent pas mais que le compte est bon, on apparie dans l'ordre.
    by_order: dict[int, AnsweredQuestion] = {}
    if not all(normalize_number(q.numero) in by_number for q in targets) and len(answered) == len(targets):
        by_order = {id(q): a for q, a in zip(targets, answered)}

    results: list[QuestionResult] = []
    used: set[int] = set()
    any_ticked = False
    for q in targets:
        visible = [p for p in q.propositions if not p.from_previous_screen]
        known_letters = [normalize_letter(p.lettre) for p in q.propositions]
        ticked = "".join(sorted(normalize_letter(p.lettre) for p in visible if p.cochee))
        any_ticked = any_ticked or bool(ticked)
        positions = {
            normalize_letter(p.lettre): (p.x, p.y) for p in visible if _valid_position(p.x, p.y, image)
        }
        a = by_order.get(id(q)) if by_order else by_number.get(normalize_number(q.numero))
        if a is None:
            result = QuestionResult(numero=normalize_number(q.numero) or "?", repondue=False)
        else:
            used.add(id(a))
            result = _result_from_answer(a, known_letters)
            result.numero = normalize_number(q.numero) or result.numero
        result.cochees = ticked
        result.complete = q.complete
        result.positions = positions
        result.suite = not q.debut_visible
        results.append(result)

    # Questions corrigées mais absentes de la lecture : on les garde, sans position.
    read_numbers = {normalize_number(q.numero) for q in reading.questions}
    for a in answered:
        if id(a) not in used and normalize_number(a.numero) not in read_numbers:
            results.append(_result_from_answer(a))
    return ScreenResult(
        questions=results,
        image_width=image.width,
        image_height=image.height,
        any_ticked=any_ticked,
    )


def correct_screen(
    image: ScreenImage,
    backend: Backend,
    searcher: Searcher | None = None,
    *,
    per_question: int = 8,
    on_progress: ProgressFn | None = None,
    previous: Callable[[], Reading | None] | None = None,
    on_reading: Callable[[Reading], None] | None = None,
) -> ScreenResult:
    """Corrige une capture d'écran. Lève ``BackendError`` si la correction est impossible.

    ``previous`` donne la lecture de la capture précédente (pour une question à
    cheval sur deux écrans) ; ``on_reading`` reçoit la lecture de celle-ci, une
    fois raccrochée à la précédente.
    """

    def progress(stage: str) -> None:
        if on_progress is not None:
            try:
                on_progress(stage)
            except Exception:
                log.exception("Erreur dans le suivi de progression")

    durations: dict[str, float] = {}
    start = time.monotonic()

    progress(STAGE_READ)
    reading: Reading | None = None
    try:
        reading, durations[STAGE_READ] = _timed(backend.read, image)
    except BackendError as exc:
        # Sans lecture, le modèle de correction lit directement la capture.
        log.warning("Lecture indisponible (%s) : correction directe", exc)

    excerpts: Sequence[Excerpt] = []
    to_answer: Reading | None = None
    if reading is not None:
        try:
            earlier = previous() if previous is not None else None
        except Exception:
            log.exception("Lecture précédente indisponible")
            earlier = None
        reading = continue_previous(reading, earlier)
        if on_reading is not None:
            try:
                on_reading(reading)
            except Exception:
                log.exception("Erreur en transmettant la lecture")
        targets = [q for q in reading.questions if is_answerable(q)]
        if not targets:
            durations["total"] = time.monotonic() - start
            return ScreenResult(
                questions=[],
                image_width=image.width,
                image_height=image.height,
                backend=backend.name,
                durations=durations,
            )
        to_answer = reading if len(targets) == len(reading.questions) else Reading(questions=targets)
        if searcher is not None and per_question > 0 and _has_courses(searcher):
            progress(STAGE_SEARCH)
            excerpts, durations[STAGE_SEARCH] = _timed(retrieve, to_answer, searcher, per_question)
            log.info("%d extrait(s) de cours retenu(s)", len(excerpts))

    progress(STAGE_ANSWER)
    answers, durations[STAGE_ANSWER] = _timed(backend.answer, image, to_answer, excerpts)

    result = merge(reading, answers, image)
    result.backend = backend.name
    durations["total"] = time.monotonic() - start
    result.durations = durations
    return result


def _has_courses(searcher: Searcher) -> bool:
    try:
        return not searcher.is_empty()
    except Exception:
        log.exception("Index de cours illisible : correction sans les cours")
        return False


def _timed(fn, *args):
    t0 = time.monotonic()
    value = fn(*args)
    return value, time.monotonic() - t0
