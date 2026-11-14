"""Point d'entrée : ``python -m correcteur`` lance l'application, les sous-commandes servent à l'installer et la vérifier."""

from __future__ import annotations

import argparse
import logging
import shutil
import sys
import tempfile
import time
from pathlib import Path

from . import __version__
from .config import CONFIG_FILENAME, Config, ConfigError, default_base_dir, load_config

log = logging.getLogger("correcteur")
EXAMPLE_CONFIG = "config.exemple.toml"


def _example_config_path() -> Path | None:
    """Fichier d'exemple livré avec le programme."""
    roots = [Path(__file__).resolve().parent.parent, default_base_dir()]
    for root in roots:
        candidate = root / EXAMPLE_CONFIG
        if candidate.is_file():
            return candidate
    return None


def prepare(base_dir: Path) -> list[str]:
    """Crée ``config.toml`` et le dossier des cours s'ils manquent. Renvoie ce qui a été fait."""
    done: list[str] = []
    base_dir.mkdir(parents=True, exist_ok=True)
    config_path = base_dir / CONFIG_FILENAME
    if not config_path.exists():
        example = _example_config_path()
        if example is not None:
            shutil.copyfile(example, config_path)
            done.append(f"{CONFIG_FILENAME} créé (réglages par défaut)")
    cfg = load_config(base_dir)
    if not cfg.courses_dir.exists():
        cfg.courses_dir.mkdir(parents=True)
        done.append(f"dossier « {cfg.cours.dossier} » créé : mets-y tes cours")
    return done


def _load(args) -> Config:
    base = Path(args.dossier).expanduser().resolve() if args.dossier else default_base_dir()
    prepare(base)
    return load_config(base)


# -- sous-commandes ----------------------------------------------------------


def cmd_run(args) -> int:
    from PySide6.QtCore import QLockFile
    from PySide6.QtWidgets import QApplication, QMessageBox

    from .app import CorrecteurApp
    from .logs import setup_logging

    qt_app = QApplication(sys.argv[:1])
    qt_app.setQuitOnLastWindowClosed(False)
    qt_app.setApplicationName("Correcteur d'annales")
    try:
        cfg = _load(args)
    except ConfigError as exc:
        # Lancé sans console, un message imprimé serait invisible.
        QMessageBox.critical(None, "Correcteur d'annales", f"Erreur dans config.toml :\n{exc}")
        return 2
    setup_logging(cfg.history_dir)
    # Sans console, une erreur non interceptée passerait inaperçue : elle va au journal.
    sys.excepthook = lambda kind, value, trace: log.error("Erreur non interceptée", exc_info=(kind, value, trace))
    # Deux exemplaires se disputeraient les mêmes raccourcis.
    lock = QLockFile(str(Path(tempfile.gettempdir()) / "correcteur-annales.lock"))
    if not lock.tryLock(200):
        QMessageBox.information(
            None, "Correcteur d'annales", "Le correcteur est déjà lancé (icône verte près de l'horloge)."
        )
        return 1
    log.info("Démarrage du correcteur %s dans %s", __version__, cfg.base_dir)
    application = CorrecteurApp(cfg)
    application.start()
    try:
        return qt_app.exec()
    finally:
        application.shutdown()
        lock.unlock()


def cmd_prepare(args) -> int:
    base = Path(args.dossier).expanduser().resolve() if args.dossier else default_base_dir()
    for line in prepare(base) or ["Rien à préparer : tout est déjà en place."]:
        print(line)
    return 0


def cmd_index(args) -> int:
    from .courses import CourseIndex

    cfg = _load(args)
    if not cfg.courses_dir.is_dir():
        print(f"Dossier des cours introuvable : {cfg.courses_dir}")
        return 1
    print(f"Indexation de {cfg.courses_dir} ...")
    started = time.monotonic()
    report = CourseIndex(cfg.index_dir).build(cfg.courses_dir, progress=print)
    print()
    print(f"Terminé en {time.monotonic() - started:.0f} s : {report.indexed} fichier(s) lus, "
          f"{report.unchanged} inchangé(s), {report.removed} retiré(s).")
    print(f"L'index contient {report.chunks} passages de cours.")
    for name, reason in report.failed:
        print(f"  Non lu : {name} ({reason})")
    if report.empty_pages:
        print(f"Attention : {report.empty_pages} page(s) sans texte (pages scannées ou images) ne sont pas "
              "consultables, dans :")
        for name in report.files_with_empty_pages[:20]:
            print(f"  - {name}")
    if report.chunks == 0:
        print("Aucun cours indexé : mets des fichiers PDF, PowerPoint, Word ou texte dans le dossier des cours.")
    return 0


def cmd_search(args) -> int:
    from .courses import CourseIndex

    cfg = _load(args)
    index = CourseIndex(cfg.index_dir)
    if index.is_empty():
        print("L'index des cours est vide : lance d'abord l'indexation.")
        return 1
    results = index.search(args.requete, limit=args.nombre)
    if not results:
        print("Aucun passage trouvé.")
    for rank, excerpt in enumerate(results, start=1):
        page = f", page {excerpt.page}" if excerpt.page else ""
        print(f"{rank}. {excerpt.source}{page}  (score {excerpt.score:.1f})")
        print("   " + " ".join(excerpt.text.split())[:400])
    return 0


def cmd_test(args) -> int:
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QImage

    from .ai.base import BackendError
    from .ai.select import make_backend
    from .capture import capture_from_image
    from .courses import CourseIndex
    from .history import format_question
    from .logs import setup_logging
    from .pipeline import correct_screen

    cfg = _load(args)
    setup_logging(None, level=logging.INFO if args.verbose else logging.WARNING)
    image = QImage(args.image)
    if image.isNull():
        print(f"Image illisible : {args.image}")
        return 1
    capture = capture_from_image(image, QRect(0, 0, image.width(), image.height()))
    try:
        backend = make_backend(cfg)
        print(f"Moteur : {backend.name} — image envoyée : {capture.image.width} × {capture.image.height}")
        result = correct_screen(
            capture.image,
            backend,
            None if args.sans_cours else CourseIndex(cfg.index_dir),
            per_question=cfg.ia.extraits_par_question,
            on_progress=lambda stage: print(f"  … {stage}", flush=True),
        )
    except BackendError as exc:
        print(f"Échec : {exc}")
        if args.verbose and exc.detail:
            print(f"  détail : {exc.detail}")
        return 1
    if not result.questions:
        print("Aucune question détectée sur l'image.")
    for question in result.questions:
        print(format_question(question, compare=result.any_ticked))
        if question.note:
            print(f"        {question.note}")
    print("Durées : " + ", ".join(f"{name} {seconds:.1f} s" for name, seconds in result.durations.items()))
    return 0


def cmd_diagnostic(args) -> int:
    from .ai.base import BackendError
    from .ai.select import make_backend
    from .courses import CourseIndex
    from .hotkeys import HotkeyError, parse_hotkey

    problems = 0

    def report(ok: bool, text: str) -> None:
        nonlocal problems
        problems += 0 if ok else 1
        print(("[OK]  " if ok else "[!!]  ") + text)

    base = Path(args.dossier).expanduser().resolve() if args.dossier else default_base_dir()
    print(f"Correcteur d'annales {__version__} — dossier : {base}")
    try:
        prepare(base)
        cfg = load_config(base)
        report(True, f"Configuration valide ({CONFIG_FILENAME})")
    except ConfigError as exc:
        report(False, f"Configuration : {exc}")
        return 1
    try:
        report(True, "Connexion à Claude : " + make_backend(cfg).check())
    except BackendError as exc:
        report(False, f"Connexion à Claude : {exc}")
    try:
        stats = CourseIndex(cfg.index_dir).stats()
        if stats.chunks:
            extra = f", {stats.empty_pages} page(s) scannée(s) non lisibles" if stats.empty_pages else ""
            report(True, f"Cours : {stats.files} fichier(s), {stats.chunks} passages indexés{extra}")
        else:
            print(f"[--]  Cours : aucun cours indexé. Mets tes fichiers dans « {cfg.courses_dir} » puis lance "
                  "l'indexation. Sans cours, Claude répond avec ses seules connaissances.")
    except Exception as exc:
        report(False, f"Index des cours illisible : {exc}")
    for name in ("corriger", "masquer", "effacer"):
        text = getattr(cfg.raccourcis, name)
        try:
            report(True, f"Raccourci « {name} » : {parse_hotkey(text)}")
        except HotkeyError as exc:
            report(False, str(exc))
    try:
        import PySide6

        report(True, f"Affichage : Qt {PySide6.__version__}, mode « {cfg.affichage.mode} »")
    except Exception as exc:
        report(False, f"Affichage : PySide6 inutilisable ({exc})")
    print()
    print("Tout est en ordre." if problems == 0 else f"{problems} point(s) à corriger ci-dessus.")
    return 0 if problems == 0 else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="correcteur",
        description="Capture l'écran d'une annale et affiche les réponses. Sans commande : lance l'application.",
    )
    parser.add_argument("--dossier", help="dossier du correcteur (config.toml, cours, index) ; par défaut le dossier courant")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="commande", metavar="commande")
    commands.add_parser("preparer", help="crée config.toml et le dossier des cours s'ils manquent")
    commands.add_parser("indexer", help="met à jour l'index de recherche des cours")
    search = commands.add_parser("chercher", help="cherche des passages dans les cours indexés")
    search.add_argument("requete")
    search.add_argument("-n", "--nombre", type=int, default=5, help="nombre de passages (5 par défaut)")
    test = commands.add_parser("tester", help="corrige une image de capture d'écran et affiche les réponses")
    test.add_argument("image")
    test.add_argument("--sans-cours", action="store_true", help="ne pas utiliser l'index des cours")
    test.add_argument("-v", "--verbose", action="store_true", help="affiche le détail technique")
    commands.add_parser("diagnostic", help="vérifie l'installation point par point")
    return parser


COMMANDS = {
    None: cmd_run,
    "preparer": cmd_prepare,
    "indexer": cmd_index,
    "chercher": cmd_search,
    "tester": cmd_test,
    "diagnostic": cmd_diagnostic,
}


def _report_startup_crash(args) -> None:
    """L'application n'a pas pu démarrer : laisse une trace lisible, même sans console."""
    import traceback

    details = traceback.format_exc()
    base = Path(args.dossier).expanduser() if args.dossier else default_base_dir()
    report = base / "erreur_demarrage.txt"
    try:
        report.write_text(details, encoding="utf-8")
    except OSError:
        pass
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox

        _app = QApplication.instance() or QApplication(sys.argv[:1])
        QMessageBox.critical(
            None,
            "Correcteur d'annales",
            f"Le correcteur n'a pas pu démarrer.\nLe détail est dans le fichier :\n{report}",
        )
    except Exception:
        pass


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return COMMANDS[args.commande](args)
    except ConfigError as exc:
        print(f"Erreur de configuration : {exc}")
        return 2
    except Exception:
        if args.commande is None:
            _report_startup_crash(args)
        raise


if __name__ == "__main__":
    sys.exit(main())
