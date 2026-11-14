"""Index de recherche des cours (SQLite FTS5, classement BM25).

Tout tient dans un fichier ``cours.sqlite``. L'indexation est incrémentale : un
fichier inchangé n'est pas relu. La recherche ne dépend d'aucun service
extérieur et répond en quelques millisecondes.
"""

from __future__ import annotations

import logging
import re
import sqlite3
import threading
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..models import Excerpt
from .extract import SUPPORTED_EXTENSIONS, Page, extract_file

log = logging.getLogger(__name__)

DB_FILENAME = "cours.sqlite"
SCHEMA_VERSION = "1"

CHUNK_MAX_CHARS = 1600
CHUNK_OVERLAP_CHARS = 200
MERGE_BELOW_CHARS = 350  # les diapositives plus courtes sont regroupées avec leurs voisines
MAX_QUERY_TERMS = 28
TITLE_WEIGHT = 0.3  # un mot du nom de fichier compte moins qu'un mot du texte

ProgressFn = Callable[[str], None]

# Mots sans valeur pour la recherche : mots-outils du français et formules
# d'énoncé (« indiquer si ces assertions sont vraies ou fausses »), qui feraient
# remonter n'importe quelle annale corrigée.
STOP_WORDS = frozenset(
    """
    a afin ai ainsi alors au aucun aucune aussi autre autres aux avec avoir car ce cela celle celles
    celui cependant ces cet cette ceux chaque chez comme comment dans de des donc dont du elle elles
    en encore entre est et etaient etait etant etc ete etre eu eux fait faire faut il ils je la le
    les leur leurs lors lorsque lui ma mais me meme mes moins mon ne ni non nos notre nous on ont ou
    par parce pas peu peut peuvent plus pour pourquoi qu quand que quel quelle quelles quels qui sa
    sans se selon ses si soit son sont sous sur ta te tes ton tous tout toute toutes tres tu un une
    vers voici vos votre vous y
    assertion assertions affirmation affirmations concernant cochez cocher exact exacte exactes
    exacts fausse fausses faux indiquer indiquez item items juste justes laquelle lequel lesquelles
    lesquels parmi proposition propositions question questions reponse reponses suivant suivante
    suivantes suivants vrai vraie vraies vrais type qcm qrm qru qroc valide
    """.split()
)

# Terminaisons retirées pour tolérer pluriels, féminins et dérivés proches
# (« potassiques » retrouve « potassique » et « potassium »). De la plus longue à la plus courte.
_SUFFIXES = (
    "ements", "ement", "ations", "ation", "iques", "ique", "euses", "euse", "eurs", "eur",
    "ales", "ale", "ees", "ee", "es", "s", "e", "x",
)
_MIN_STEM = 4
_TOKEN = re.compile(r"[0-9a-z]+")


def fold(text: str) -> str:
    """Minuscules sans accents, comme le fait l'index."""
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def query_terms(query: str) -> list[tuple[str, bool]]:
    """Termes de recherche d'une requête : ``(terme, recherche_par_préfixe)``.

    Les sigles et mots courts (« QT », « Gs », « IgE », « pH ») sont gardés
    tels quels ; seules les lettres isolées sont écartées.
    """
    terms: list[tuple[str, bool]] = []
    seen: set[str] = set()
    for token in _TOKEN.findall(fold(query)):
        if token in STOP_WORDS or (len(token) < 2 and not token.isdigit()):
            continue
        prefix = False
        if not token.isdigit() and len(token) >= 5:
            prefix = True
            if token.endswith("aux"):
                # « canaux » -> « cana », qui retrouve aussi « canal ».
                token = token[:-2]
            else:
                for suffix in _SUFFIXES:
                    if token.endswith(suffix) and len(token) - len(suffix) >= _MIN_STEM:
                        token = token[: -len(suffix)]
                        break
        if token not in seen:
            seen.add(token)
            terms.append((token, prefix))
        if len(terms) >= MAX_QUERY_TERMS:
            break
    return terms


def fts_query(query: str) -> str:
    """Expression FTS5 : les termes reliés par OU, les plus longs en préfixe.

    Les termes ne contiennent que des lettres et des chiffres : rien dans la
    requête d'origine ne peut être interprété comme un opérateur.
    """
    return " OR ".join(f'"{term}"*' if prefix else f'"{term}"' for term, prefix in query_terms(query))


def _split_long(text: str) -> list[str]:
    """Découpe un texte trop long en morceaux qui se recouvrent légèrement."""
    if len(text) <= CHUNK_MAX_CHARS:
        return [text]
    pieces: list[str] = []
    units = re.split(r"(?<=[.!?;:])\s+|\n+", text)
    current = ""
    for unit in units:
        unit = unit.strip()
        if not unit:
            continue
        while len(unit) > CHUNK_MAX_CHARS:  # phrase interminable : coupe nette
            if current:
                pieces.append(current)
                current = ""
            pieces.append(unit[:CHUNK_MAX_CHARS])
            unit = unit[CHUNK_MAX_CHARS - CHUNK_OVERLAP_CHARS :]
        if current and len(current) + len(unit) + 1 > CHUNK_MAX_CHARS:
            pieces.append(current)
            tail = current[-CHUNK_OVERLAP_CHARS:]
            cut = tail.find(" ")
            current = tail[cut + 1 :] if cut >= 0 else ""
        current = f"{current} {unit}".strip() if current else unit
    if current:
        pieces.append(current)
    return pieces


def chunk_pages(pages: list[Page]) -> list[tuple[int, str]]:
    """Transforme des pages en passages ``(page de début, texte)``."""
    chunks: list[tuple[int, str]] = []
    buffer = ""
    start = 0

    def flush() -> None:
        nonlocal buffer
        if buffer:
            chunks.extend((start, piece) for piece in _split_long(buffer))
            buffer = ""

    for page in pages:
        text = page.text.strip()
        if not text:
            continue
        short = len(text) < MERGE_BELOW_CHARS or len(buffer) < MERGE_BELOW_CHARS
        if buffer and short and len(buffer) + len(text) + 1 <= CHUNK_MAX_CHARS:
            buffer = f"{buffer}\n{text}"
            continue
        flush()
        buffer, start = text, page.number
    flush()
    return chunks


@dataclass
class BuildReport:
    indexed: int = 0  # fichiers lus ou relus
    unchanged: int = 0
    removed: int = 0
    failed: list[tuple[str, str]] = field(default_factory=list)  # (fichier, raison)
    chunks: int = 0  # total de passages dans l'index
    empty_pages: int = 0  # pages sans texte exploitable (scans)
    files_with_empty_pages: list[str] = field(default_factory=list)


@dataclass
class IndexStats:
    files: int = 0
    chunks: int = 0
    pages: int = 0
    empty_pages: int = 0
    failed_files: int = 0


class CourseIndex:
    """Index des cours. Sûr à utiliser depuis plusieurs fils d'exécution."""

    def __init__(self, index_dir: Path) -> None:
        self.index_dir = Path(index_dir)
        self.db_path = self.index_dir / DB_FILENAME
        self._local = threading.local()

    # -- connexions --------------------------------------------------------
    def _connect(self) -> sqlite3.Connection:
        """Une connexion par fil d'exécution (SQLite n'aime pas le partage)."""
        conn = getattr(self._local, "conn", None)
        if conn is None:
            self.index_dir.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self.db_path, timeout=30)
            self._ensure_schema(conn)
            self._local.conn = conn
        return conn

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    @staticmethod
    def _ensure_schema(conn: sqlite3.Connection) -> None:
        conn.execute("CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT)")
        row = conn.execute("SELECT value FROM meta WHERE key = 'schema'").fetchone()
        if row is not None and row[0] != SCHEMA_VERSION:
            conn.execute("DROP TABLE IF EXISTS chunks")
            conn.execute("DROP TABLE IF EXISTS files")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS files("
            "id INTEGER PRIMARY KEY, path TEXT UNIQUE, size INTEGER, mtime_ns INTEGER, "
            "pages INTEGER, empty_pages INTEGER, error TEXT)"
        )
        try:
            conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS chunks USING fts5("
                "text, title, file_id UNINDEXED, page UNINDEXED, "
                "tokenize = 'unicode61 remove_diacritics 2')"
            )
        except sqlite3.OperationalError as exc:
            raise RuntimeError(
                "Cette installation de Python ne fournit pas la recherche plein texte (FTS5)."
            ) from exc
        conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('schema', ?)", (SCHEMA_VERSION,))
        conn.commit()

    # -- construction ------------------------------------------------------
    def build(self, courses_dir: Path, progress: ProgressFn | None = None) -> BuildReport:
        """Met l'index à jour d'après le contenu de ``courses_dir`` (sous-dossiers compris)."""
        courses_dir = Path(courses_dir)
        report = BuildReport()

        def say(message: str) -> None:
            if progress is not None:
                progress(message)

        conn = self._connect()
        found: dict[str, Path] = {}
        if courses_dir.is_dir():
            for path in sorted(courses_dir.rglob("*")):
                # Les fichiers « ~$... » sont les verrous temporaires d'Office.
                if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS and not path.name.startswith("~$"):
                    found[path.relative_to(courses_dir).as_posix()] = path

        known = {
            row[0]: (row[1], row[2], row[3])
            for row in conn.execute("SELECT path, id, size, mtime_ns FROM files")
        }
        for rel in sorted(set(known) - set(found)):
            self._remove(conn, known[rel][0])
            report.removed += 1
            say(f"Retiré : {rel}")
        conn.commit()

        for number, (rel, path) in enumerate(found.items(), start=1):
            stat = path.stat()
            previous = known.get(rel)
            if previous and previous[1] == stat.st_size and previous[2] == stat.st_mtime_ns:
                report.unchanged += 1
                continue
            say(f"[{number}/{len(found)}] {rel}")
            extraction = extract_file(path)
            if previous:
                self._remove(conn, previous[0])
            cursor = conn.execute(
                "INSERT INTO files(path, size, mtime_ns, pages, empty_pages, error) VALUES(?,?,?,?,?,?)",
                (rel, stat.st_size, stat.st_mtime_ns, len(extraction.pages), extraction.empty_pages, extraction.error),
            )
            title = Path(rel).stem
            conn.executemany(
                "INSERT INTO chunks(text, title, file_id, page) VALUES(?,?,?,?)",
                [(text, title, cursor.lastrowid, page) for page, text in chunk_pages(extraction.pages)],
            )
            conn.commit()  # un fichier à la fois : une interruption ne perd pas le travail fait
            if extraction.error:
                report.failed.append((rel, extraction.error))
                say(f"    non lu : {extraction.error}")
            else:
                report.indexed += 1

        report.chunks = conn.execute("SELECT count(*) FROM chunks").fetchone()[0]
        rows = conn.execute("SELECT path, empty_pages FROM files WHERE empty_pages > 0 ORDER BY path").fetchall()
        report.empty_pages = sum(row[1] for row in rows)
        report.files_with_empty_pages = [row[0] for row in rows]
        return report

    @staticmethod
    def _remove(conn: sqlite3.Connection, file_id: int) -> None:
        conn.execute("DELETE FROM chunks WHERE file_id = ?", (file_id,))
        conn.execute("DELETE FROM files WHERE id = ?", (file_id,))

    # -- recherche ---------------------------------------------------------
    def is_empty(self) -> bool:
        if not self.db_path.exists():
            return True
        return self._connect().execute("SELECT 1 FROM chunks LIMIT 1").fetchone() is None

    def search(self, query: str, limit: int = 8) -> list[Excerpt]:
        """Passages les plus pertinents pour ``query``, du meilleur au moins bon."""
        expression = fts_query(query)
        if not expression or limit <= 0 or not self.db_path.exists():
            return []
        try:
            rows = self._connect().execute(
                "SELECT chunks.rowid, files.path, chunks.page, chunks.text, "
                "bm25(chunks, 1.0, ?) AS rank "
                "FROM chunks JOIN files ON files.id = chunks.file_id "
                "WHERE chunks MATCH ? ORDER BY rank LIMIT ?",
                (TITLE_WEIGHT, expression, limit),
            ).fetchall()
        except sqlite3.Error:
            log.exception("Recherche impossible dans l'index des cours")
            return []
        # bm25() est d'autant plus négatif que le passage est pertinent.
        return [Excerpt(source=r[1], page=int(r[2] or 0), text=r[3], score=-float(r[4]), chunk_id=int(r[0])) for r in rows]

    def stats(self) -> IndexStats:
        if not self.db_path.exists():
            return IndexStats()
        conn = self._connect()
        files, pages, empty, failed = conn.execute(
            "SELECT count(*), coalesce(sum(pages), 0), coalesce(sum(empty_pages), 0), "
            "coalesce(sum(error != ''), 0) FROM files"
        ).fetchone()
        chunks = conn.execute("SELECT count(*) FROM chunks").fetchone()[0]
        return IndexStats(files=files, chunks=chunks, pages=pages, empty_pages=empty, failed_files=failed)
