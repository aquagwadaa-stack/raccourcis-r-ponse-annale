"""Enchaînement complet pour une capture : lecture -> recherche dans les cours -> correction.

Ce module ne dépend ni de l'interface ni d'un moteur précis : il reçoit un
``Backend`` et, s'il existe, un index de cours.
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Protocol, Sequence

from .ai.base import Backend, BackendError
from .models import (
    AnsweredQuestion,
    Answers,
    Excerpt,
    QuestionResult,
    Reading,
    ReadQuestion,
    ScreenImage,
    ScreenResult,
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
    """Passages de cours pour toutes les questions complètes d'une capture, sans doublon."""
    per_q = [
        retrieve_for_question(q, searcher, per_question) for q in reading.questions if q.complete
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


def merge(reading: Reading | None, answers: Answers | None, image: ScreenImage) -> ScreenResult:
    """Réunit lecture et correction en un résultat affichable."""
    answered = list(answers.questions) if answers else []
    by_number = {normalize_number(a.numero): a for a in answered}
    results: list[QuestionResult] = []
    any_ticked = False

    if reading is None or not reading.questions:
        for a in answered:
            letters = sorted({normalize_letter(v.lettre) for v in a.verdicts if v.correcte})
            results.append(
                QuestionResult(
                    numero=normalize_number(a.numero) or a.numero,
                    lettres="".join(letters),
                    reponse_libre=a.reponse_libre.strip(),
                    cochees=None,
                    source=a.source,
                    confiance=a.confiance,
                    note=a.note.strip(),
                )
            )
        return ScreenResult(questions=results, image_width=image.width, image_height=image.height)

    complete_questions = [q for q in reading.questions if q.complete]
    # Si les numéros ne se correspondent pas mais que le compte est bon, on apparie dans l'ordre.
    by_order: dict[int, AnsweredQuestion] = {}
    numbers_match = all(normalize_number(q.numero) in by_number for q in complete_questions)
    if not numbers_match and len(answered) == len(complete_questions):
        by_order = {id(q): a for q, a in zip(complete_questions, answered)}

    used: set[int] = set()
    for q in reading.questions:
        number = normalize_number(q.numero) or q.numero
        known_letters = [normalize_letter(p.lettre) for p in q.propositions]
        ticked = "".join(sorted(normalize_letter(p.lettre) for p in q.propositions if p.cochee))
        any_ticked = any_ticked or bool(ticked)
        positions = {
            normalize_letter(p.lettre): (p.x, p.y)
            for p in q.propositions
            if _valid_position(p.x, p.y, image)
        }
        result = QuestionResult(
            numero=number, cochees=ticked, complete=q.complete, positions=positions, repondue=False
        )
        a = by_order.get(id(q)) if by_order else by_number.get(normalize_number(q.numero))
        if q.complete and a is not None:
            used.add(id(a))
            letters = {normalize_letter(v.lettre) for v in a.verdicts if v.correcte}
            if known_letters:
                letters &= set(known_letters)
            result.lettres = "".join(sorted(letters))
            result.reponse_libre = a.reponse_libre.strip()
            result.source = a.source
            result.confiance = a.confiance
            result.note = a.note.strip()
            result.repondue = True
        results.append(result)

    # Questions corrigées mais absentes de la lecture : on les garde, sans position.
    for a in answered:
        if id(a) in used or normalize_number(a.numero) in {normalize_number(q.numero) for q in reading.questions}:
            continue
        letters = sorted({normalize_letter(v.lettre) for v in a.verdicts if v.correcte})
        results.append(
            QuestionResult(
                numero=normalize_number(a.numero) or a.numero,
                lettres="".join(letters),
                reponse_libre=a.reponse_libre.strip(),
                cochees=None,
                source=a.source,
                confiance=a.confiance,
                note=a.note.strip(),
            )
        )
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
) -> ScreenResult:
    """Corrige une capture d'écran. Lève ``BackendError`` si la correction est impossible."""

    def progress(stage: str) -> None:
        if on_progress is not None:
            try:
                on_progress(stage)
            except Exception:
                log.exception("Erreur dans le suivi de progression")

    durations: dict[str, float] = {}
    start = time.monotonic()
    has_courses = False
    if searcher is not None and per_question > 0:
        try:
            has_courses = not searcher.is_empty()
        except Exception:
            log.exception("Index de cours illisible : correction sans les cours")

    reading: Reading | None = None
    answers: Answers | None = None

    if not has_courses:
        # Sans cours, lecture et correction sont indépendantes : on les lance ensemble.
        progress(STAGE_ANSWER)
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="correcteur") as pool:
            read_future = pool.submit(_timed, backend.read, image)
            answer_future = pool.submit(_timed, backend.answer, image, None, [])
            answers, durations[STAGE_ANSWER] = answer_future.result()
            try:
                reading, durations[STAGE_READ] = read_future.result()
            except BackendError as exc:
                # La lecture ne sert ici qu'à placer les coches : on s'en passe.
                log.warning("Lecture indisponible (%s) : réponses affichées sans positions", exc)
    else:
        progress(STAGE_READ)
        try:
            reading, durations[STAGE_READ] = _timed(backend.read, image)
        except BackendError as exc:
            log.warning("Lecture indisponible (%s) : correction directe sans les cours", exc)
        excerpts: Sequence[Excerpt] = []
        if reading is not None:
            if not reading.questions:
                durations["total"] = time.monotonic() - start
                return ScreenResult(
                    questions=[],
                    image_width=image.width,
                    image_height=image.height,
                    backend=backend.name,
                    durations=durations,
                )
            if any(q.complete for q in reading.questions):
                progress(STAGE_SEARCH)
                assert searcher is not None
                excerpts, durations[STAGE_SEARCH] = _timed(retrieve, reading, searcher, per_question)
                log.info("%d extrait(s) de cours retenu(s)", len(excerpts))
        if reading is None or any(q.complete for q in reading.questions):
            progress(STAGE_ANSWER)
            answers, durations[STAGE_ANSWER] = _timed(backend.answer, image, reading, excerpts)

    result = merge(reading, answers, image)
    result.backend = backend.name
    durations["total"] = time.monotonic() - start
    result.durations = durations
    return result


def _timed(fn, *args):
    t0 = time.monotonic()
    value = fn(*args)
    return value, time.monotonic() - t0
