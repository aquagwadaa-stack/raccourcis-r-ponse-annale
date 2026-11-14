"""Moteur « Claude Code » : utilise l'abonnement Claude de l'étudiant, sans clé API.

Chaque phase est un appel sans outil au programme ``claude`` en mode non
interactif. L'image part sur l'entrée standard (JSON), les consignes dans un
fichier temporaire, et la réponse revient validée par un schéma JSON.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Sequence, TypeVar

from pydantic import BaseModel, ValidationError

from ..config import AIConfig
from ..models import Answers, Excerpt, Reading, ScreenImage
from . import prompts
from .base import BackendError

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

VERSION_TIMEOUT_S = 20
INSTALL_HINT = (
    "Claude Code est introuvable. Installe-le (voir le README, rubrique « Connexion à Claude »), "
    "puis lance « claude » une fois dans un terminal pour te connecter."
)
LOGIN_HINT = (
    "Claude Code n'est pas connecté : ouvre un terminal, lance « claude » et connecte-toi avec ton compte."
)

_LOGIN_MARKERS = ("/login", "not logged in", "invalid api key", "authentication_error", "oauth token", "unauthorized")
_LIMIT_MARKERS = ("limit", "quota")


def compact_schema(model: type[BaseModel]) -> str:
    """Schéma JSON réduit aux types, en ASCII et sans espace.

    Il passe en argument de ligne de commande. Sous Windows, ``claude`` peut
    être un script ``.cmd`` : la ligne traverse alors ``cmd.exe``, qui malmène
    accents, retours à la ligne et caractères spéciaux. Les descriptions sont
    donc retirées (les consignes les donnent déjà) pour ne garder que des
    caractères sans risque.
    """

    def strip(node: Any) -> Any:
        if isinstance(node, dict):
            return {k: strip(v) for k, v in node.items() if k not in ("description", "title")}
        if isinstance(node, list):
            return [strip(v) for v in node]
        return node

    return json.dumps(strip(model.model_json_schema()), ensure_ascii=True, separators=(",", ":"))


def find_executable(command: str) -> str | None:
    """Chemin du programme ``claude``, ou ``None`` s'il est introuvable."""
    found = shutil.which(command)
    if found:
        return found
    if Path(command).name != command:  # un chemin explicite qui n'existe pas
        return None
    home = Path.home()
    candidates = [home / ".local" / "bin" / command, home / ".claude" / "local" / command]
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA", "")
        candidates = [
            home / ".local" / "bin" / f"{command}.exe",
            *([Path(appdata) / "npm" / f"{command}.cmd"] if appdata else []),
        ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return None


def _extract_json(text: str) -> Any:
    """Retrouve l'objet JSON d'une réponse texte (éventuellement entourée de ``` ou de phrases)."""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("aucun objet JSON dans la réponse")
    return json.loads(text[start : end + 1])


def _kill_tree(process: subprocess.Popen) -> None:
    """Arrête le programme et ses processus enfants."""
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                capture_output=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                timeout=10,
            )
        else:
            os.killpg(process.pid, signal.SIGKILL)
    except (OSError, subprocess.SubprocessError):
        process.kill()


class CliBackend:
    """Appelle Claude Code en ligne de commande (``claude -p``)."""

    name = "claude_cli"

    def __init__(self, cfg: AIConfig) -> None:
        self.cfg = cfg
        self._executable: str | None = None
        self._workdir: str | None = None

    # -- contrat Backend ---------------------------------------------------
    def read(self, image: ScreenImage) -> Reading:
        model = self.cfg.cli_modele_lecture or self.cfg.cli_modele
        return self._call(prompts.READ_SYSTEM, prompts.read_user_text(image), image, Reading, model)

    def answer(self, image: ScreenImage, reading: Reading | None, excerpts: Sequence[Excerpt]) -> Answers:
        text = prompts.answer_user_text(reading, excerpts, justifications=self.cfg.justifications)
        return self._call(prompts.ANSWER_SYSTEM, text, image, Answers, self.cfg.cli_modele)

    def check(self) -> str:
        executable = self._find()
        try:
            done = subprocess.run(
                [executable, "--version"],
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                timeout=VERSION_TIMEOUT_S,
                **self._platform_options(),
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise BackendError("Claude Code ne démarre pas.", detail=repr(exc)) from exc
        version = (done.stdout or done.stderr).strip().splitlines()[0:1]
        if done.returncode != 0 or not version:
            raise BackendError("Claude Code ne démarre pas.", detail=(done.stderr or done.stdout)[:500])
        return f"Claude Code {version[0]} (abonnement Claude) : {executable}"

    # -- mécanique ---------------------------------------------------------
    def _find(self) -> str:
        if self._executable is None:
            self._executable = find_executable(self.cfg.cli_commande)
        if self._executable is None:
            raise BackendError(INSTALL_HINT)
        return self._executable

    def _neutral_dir(self) -> str:
        """Dossier vide d'où lancer ``claude``, pour qu'aucun réglage de projet ne soit chargé."""
        if self._workdir is None or not os.path.isdir(self._workdir):
            self._workdir = tempfile.mkdtemp(prefix="correcteur-claude-")
        return self._workdir

    @staticmethod
    def _platform_options() -> dict[str, Any]:
        if sys.platform == "win32":
            # Sans cela, une fenêtre de console clignote à chaque appel.
            return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
        return {"start_new_session": True}  # permet d'arrêter tout le groupe de processus

    def _command(self, executable: str, system_file: str, schema: str, model: str) -> list[str]:
        command = [
            executable,
            "-p",
            "--input-format", "stream-json",
            "--output-format", "stream-json",
            "--verbose",  # exigé par la sortie stream-json
            "--tools", "",  # simple appel au modèle : aucun outil, donc aucun accès au PC
            "--strict-mcp-config",
            "--no-session-persistence",
            "--system-prompt-file", system_file,
            "--json-schema", schema,
        ]
        if model:
            command += ["--model", model]
        return command

    @staticmethod
    def _stdin_payload(image: ScreenImage, text: str) -> str:
        message = {
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": base64.standard_b64encode(image.png).decode("ascii"),
                        },
                    },
                    {"type": "text", "text": text},
                ],
            },
        }
        return json.dumps(message, ensure_ascii=True) + "\n"

    def _call(self, system: str, text: str, image: ScreenImage, output: type[T], model: str) -> T:
        executable = self._find()
        timeout = max(10, int(self.cfg.delai_max_s))
        workdir = self._neutral_dir()
        system_file = os.path.join(workdir, f"consignes-{output.__name__.lower()}.txt")
        Path(system_file).write_text(system, encoding="utf-8")
        command = self._command(executable, system_file, compact_schema(output), model)
        # Avec une clé API dans l'environnement, Claude Code facturerait l'API au
        # lieu d'utiliser l'abonnement : ce moteur est justement le choix « abonnement ».
        env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
        started = time.monotonic()
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=workdir,
                env=env,
                encoding="utf-8",
                errors="replace",
                **self._platform_options(),
            )
        except FileNotFoundError as exc:
            self._executable = None
            raise BackendError(INSTALL_HINT, detail=repr(exc)) from exc
        except OSError as exc:
            raise BackendError("Claude Code ne démarre pas.", detail=repr(exc)) from exc
        try:
            stdout, stderr = process.communicate(self._stdin_payload(image, text), timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            _kill_tree(process)
            try:
                process.communicate(timeout=5)
            except (subprocess.SubprocessError, OSError):
                pass
            raise BackendError(f"Claude n'a pas répondu en {timeout} s.", detail="délai dépassé") from exc
        elapsed = time.monotonic() - started
        result = self._result_event(stdout)
        if result is None:
            raise self._failure(f"{stdout[-1500:]}\n{stderr[-1500:]}", process.returncode)
        if result.get("is_error") or result.get("subtype") not in (None, "success"):
            raise self._failure(f"{result.get('result') or ''}\n{stderr[-1500:]}", process.returncode, result)
        log.info(
            "claude -p (%s) : %.1f s, modèle(s) %s",
            output.__name__,
            elapsed,
            ", ".join((result.get("modelUsage") or {}).keys()) or "?",
        )
        return self._parse(result, output)

    @staticmethod
    def _result_event(stdout: str) -> dict[str, Any] | None:
        """Dernier événement ``result`` de la sortie (un objet JSON par ligne)."""
        found: dict[str, Any] | None = None
        for line in stdout.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict) and event.get("type") == "result":
                found = event
        return found

    @staticmethod
    def _parse(result: dict[str, Any], output: type[T]) -> T:
        candidates: list[Any] = []
        if isinstance(result.get("structured_output"), dict):
            candidates.append(result["structured_output"])
        if isinstance(result.get("result"), str) and result["result"].strip():
            try:
                candidates.append(_extract_json(result["result"]))
            except ValueError:
                pass
        last_error = "aucune donnée structurée dans la réponse"
        for candidate in candidates:
            try:
                return output.model_validate(candidate)
            except ValidationError as exc:
                last_error = str(exc)[:600]
        raise BackendError("Réponse de Claude illisible, réessaie.", detail=last_error)

    @staticmethod
    def _failure(text: str, returncode: int | None, result: dict[str, Any] | None = None) -> BackendError:
        detail = f"code {returncode} : {text.strip()[:1500]}"
        lowered = text.lower()
        status = (result or {}).get("api_error_status")
        if status == 401 or any(marker in lowered for marker in _LOGIN_MARKERS):
            return BackendError(LOGIN_HINT, detail=detail)
        if status == 429 or any(marker in lowered for marker in _LIMIT_MARKERS):
            reset = re.search(r"resets?[^\n.]{0,40}", text, flags=re.IGNORECASE)
            suffix = f" ({reset.group(0).strip()})" if reset else ""
            return BackendError(f"Limite d'utilisation de ton abonnement Claude atteinte{suffix}.", detail=detail)
        return BackendError("Claude Code a renvoyé une erreur (détail dans le journal).", detail=detail)
