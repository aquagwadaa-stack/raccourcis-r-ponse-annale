"""Configuration de l'application.

Tout est lu dans ``config.toml`` (dans le dossier de l'application). Chaque
valeur a un défaut : le fichier peut être absent ou partiel. La clé API ne va
jamais dans ``config.toml`` : elle vient de la variable d'environnement
``ANTHROPIC_API_KEY``, ou du fichier ``cle_api.txt`` / ``.env`` placé dans le
dossier de l'application.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any

CONFIG_FILENAME = "config.toml"
API_KEY_FILENAME = "cle_api.txt"
ENV_BASE_DIR = "CORRECTEUR_DOSSIER"

DISPLAY_MODES = ("lettres", "coches", "les_deux")
BACKENDS = ("auto", "api", "claude_cli")
EFFORTS = ("low", "medium", "high", "xhigh", "max")
POSITIONS = ("bas_droite", "bas_gauche", "haut_droite", "haut_gauche")


class ConfigError(ValueError):
    """Erreur de configuration, avec un message lisible par l'utilisateur."""


@dataclass
class HotkeyConfig:
    corriger: str = "f8"  # capture l'écran et lance la correction
    masquer: str = "f9"  # affiche / masque les réponses
    effacer: str = "shift+f8"  # vide la liste (nouvelle annale)


@dataclass
class DisplayConfig:
    mode: str = "lettres"  # "lettres" | "coches" | "les_deux"
    position: str = "bas_droite"
    taille_police: int = 20
    opacite: float = 0.92
    duree_coches_s: int = 30  # les coches disparaissent après ce délai (0 = jamais)
    comparer_mes_reponses: bool = True  # signale les différences avec les cases déjà cochées


@dataclass
class AIConfig:
    backend: str = "auto"  # "auto" | "api" | "claude_cli"
    # --- API Anthropic (clé API) ---
    modele_reponse: str = "claude-opus-5-5"
    modele_lecture: str = "claude-opus-5-5"
    effort_reponse: str = "high"
    effort_lecture: str = "low"
    mode_rapide: bool = False  # sortie ~2,5x plus rapide, tarif doublé (modèles compatibles)
    # --- Claude Code en ligne de commande (abonnement Claude, sans clé API) ---
    cli_commande: str = "claude"
    cli_modele: str = ""  # modèle qui corrige ; vide = modèle par défaut du compte
    # Lire la capture est une tâche simple : un modèle léger la fait aussi bien, plus vite,
    # et entame beaucoup moins le quota de l'abonnement. Vide = même modèle que cli_modele.
    cli_modele_lecture: str = "haiku"
    # --- commun ---
    extraits_par_question: int = 8
    justifications: bool = False  # ajoute une phrase d'explication dans l'historique
    delai_max_s: int = 150


@dataclass
class CoursesConfig:
    dossier: str = "cours"
    index: str = "index"


@dataclass
class HistoryConfig:
    actif: bool = True
    dossier: str = "historique"
    garder_captures: bool = False


@dataclass
class Config:
    base_dir: Path = field(default_factory=Path.cwd)
    raccourcis: HotkeyConfig = field(default_factory=HotkeyConfig)
    affichage: DisplayConfig = field(default_factory=DisplayConfig)
    ia: AIConfig = field(default_factory=AIConfig)
    cours: CoursesConfig = field(default_factory=CoursesConfig)
    historique: HistoryConfig = field(default_factory=HistoryConfig)

    # -- chemins résolus ---------------------------------------------------
    def _resolve(self, value: str) -> Path:
        path = Path(value).expanduser()
        return path if path.is_absolute() else self.base_dir / path

    @property
    def courses_dir(self) -> Path:
        return self._resolve(self.cours.dossier)

    @property
    def index_dir(self) -> Path:
        return self._resolve(self.cours.index)

    @property
    def history_dir(self) -> Path:
        return self._resolve(self.historique.dossier)

    @property
    def config_path(self) -> Path:
        return self.base_dir / CONFIG_FILENAME

    # -- validation --------------------------------------------------------
    def validate(self) -> None:
        if self.affichage.mode not in DISPLAY_MODES:
            raise ConfigError(
                f"affichage.mode = {self.affichage.mode!r} : valeurs possibles {', '.join(DISPLAY_MODES)}"
            )
        if self.affichage.position not in POSITIONS:
            raise ConfigError(
                f"affichage.position = {self.affichage.position!r} : valeurs possibles {', '.join(POSITIONS)}"
            )
        if self.ia.backend not in BACKENDS:
            raise ConfigError(
                f"ia.backend = {self.ia.backend!r} : valeurs possibles {', '.join(BACKENDS)}"
            )
        for name in ("effort_reponse", "effort_lecture"):
            value = getattr(self.ia, name)
            if value not in EFFORTS:
                raise ConfigError(f"ia.{name} = {value!r} : valeurs possibles {', '.join(EFFORTS)}")
        if not 0.2 <= self.affichage.opacite <= 1.0:
            raise ConfigError("affichage.opacite doit être compris entre 0.2 et 1.0")
        if self.ia.extraits_par_question < 0:
            raise ConfigError("ia.extraits_par_question ne peut pas être négatif")


def default_base_dir() -> Path:
    """Dossier de l'application : variable ``CORRECTEUR_DOSSIER``, sinon dossier courant."""
    env = os.environ.get(ENV_BASE_DIR)
    if env:
        return Path(env).expanduser().resolve()
    return Path.cwd()


def _apply(section: Any, values: dict[str, Any], path: str) -> None:
    known = {f.name: f for f in fields(section)}
    for key, value in values.items():
        if key not in known:
            raise ConfigError(f"Option inconnue dans config.toml : [{path}] {key}")
        current = getattr(section, key)
        if is_dataclass(current):
            if not isinstance(value, dict):
                raise ConfigError(f"[{path}.{key}] doit être une section")
            _apply(current, value, f"{path}.{key}")
            continue
        expected = type(current)
        if expected is float and isinstance(value, int) and not isinstance(value, bool):
            value = float(value)
        if expected is not type(value):
            raise ConfigError(
                f"[{path}] {key} : valeur {value!r} invalide (attendu : {expected.__name__})"
            )
        setattr(section, key, value)


def load_config(base_dir: Path | None = None) -> Config:
    """Charge ``config.toml`` depuis ``base_dir`` (défauts si le fichier est absent)."""
    base = Path(base_dir) if base_dir is not None else default_base_dir()
    cfg = Config(base_dir=base)
    path = base / CONFIG_FILENAME
    if path.exists():
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8-sig"))
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"config.toml illisible : {exc}") from exc
        for name, values in data.items():
            if name not in ("raccourcis", "affichage", "ia", "cours", "historique"):
                raise ConfigError(f"Section inconnue dans config.toml : [{name}]")
            if not isinstance(values, dict):
                raise ConfigError(f"[{name}] doit être une section")
            _apply(getattr(cfg, name), values, name)
    cfg.validate()
    return cfg


def load_api_key(base_dir: Path) -> str | None:
    """Renvoie la clé API si elle est disponible, et la place dans l'environnement.

    Ordre : ``ANTHROPIC_API_KEY`` déjà définie, puis ``cle_api.txt``, puis
    ``.env`` (ligne ``ANTHROPIC_API_KEY=...``). La clé n'est jamais journalisée.
    """
    existing = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if existing:
        return existing
    key: str | None = None
    key_file = base_dir / API_KEY_FILENAME
    if key_file.exists():
        for line in key_file.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                key = line.split("=", 1)[1].strip() if line.startswith("ANTHROPIC_API_KEY=") else line
                break
    if key is None:
        env_file = base_dir / ".env"
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8-sig").splitlines():
                line = line.strip()
                if line.startswith("ANTHROPIC_API_KEY="):
                    key = line.split("=", 1)[1].strip()
                    break
    if key:
        key = key.strip().strip('"').strip("'")
    if key:
        os.environ["ANTHROPIC_API_KEY"] = key
        return key
    return None
