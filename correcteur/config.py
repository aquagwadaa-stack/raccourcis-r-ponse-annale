"""Configuration de l'application.

Tout est lu dans ``config.toml`` (dans le dossier de l'application). Chaque
valeur a un défaut : le fichier peut être absent ou partiel. La clé API ne va
jamais dans ``config.toml`` : elle vient de la variable d'environnement
``ANTHROPIC_API_KEY``, ou du fichier ``cle_api.txt`` / ``.env`` placé dans le
dossier de l'application.
"""

from __future__ import annotations

import json
import os
import re
import tomllib
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any

CONFIG_FILENAME = "config.toml"
EXAMPLE_FILENAME = "config.exemple.toml"
BACKUP_FILENAME = "config.ancien.toml"
API_KEY_FILENAME = "cle_api.txt"
ENV_BASE_DIR = "CORRECTEUR_DOSSIER"
# Version du format de config.toml : à augmenter quand des valeurs par défaut changent,
# pour que migrate_config remplace les anciennes valeurs par défaut restées dans le fichier.
CONFIG_VERSION = 2

DISPLAY_MODES = ("lettres", "coches", "les_deux")
LAYOUTS = ("colonne", "ligne")
BACKENDS = ("auto", "api", "claude_cli")
EFFORTS = ("low", "medium", "high", "xhigh", "max")
POSITIONS = ("bas_droite", "bas_gauche", "haut_droite", "haut_gauche")


class ConfigError(ValueError):
    """Erreur de configuration, avec un message lisible par l'utilisateur."""


@dataclass
class HotkeyConfig:
    corriger: str = "!"  # capture l'écran et lance la correction
    masquer: str = "f9"  # affiche / masque les réponses
    effacer: str = "shift+f8"  # vide la liste (nouvelle annale)


@dataclass
class DisplayConfig:
    mode: str = "lettres"  # "lettres" | "coches" | "les_deux"
    position: str = "bas_droite"
    taille_police: int = 4  # choisi par l'étudiant ; se change depuis l'icône
    opacite: float = 0.9
    reponses_max: int = 2  # les plus anciennes disparaissent au-delà
    disposition: str = "colonne"  # "colonne" : une question par ligne ; "ligne" : tout sur une ligne
    duree_coches_s: int = 30  # les coches disparaissent après ce délai (0 = jamais)
    comparer_mes_reponses: bool = True  # coches : croix rouge sur une case cochée à tort


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
        if self.affichage.disposition not in LAYOUTS:
            raise ConfigError(
                f"affichage.disposition = {self.affichage.disposition!r} : valeurs possibles {', '.join(LAYOUTS)}"
            )
        if not 0.2 <= self.affichage.opacite <= 1.0:
            raise ConfigError("affichage.opacite doit être compris entre 0.2 et 1.0")
        if not 1 <= self.affichage.taille_police <= 40:
            raise ConfigError("affichage.taille_police doit être compris entre 1 et 40")
        if self.affichage.reponses_max < 1:
            raise ConfigError("affichage.reponses_max doit valoir au moins 1")
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
            if name == "version":
                continue
            if name not in ("raccourcis", "affichage", "ia", "cours", "historique"):
                raise ConfigError(f"Section inconnue dans config.toml : [{name}]")
            if not isinstance(values, dict):
                raise ConfigError(f"[{name}] doit être une section")
            _apply(getattr(cfg, name), values, name)
    cfg.validate()
    return cfg


# Valeurs par défaut des versions précédentes, telles que config.exemple.toml les a
# recopiées dans config.toml : à la migration, elles cèdent la place aux nouvelles.
LEGACY_DEFAULTS: dict[tuple[str, str], tuple[Any, ...]] = {
    ("raccourcis", "corriger"): ("f8",),
    ("raccourcis", "masquer"): ("f9",),
    ("raccourcis", "effacer"): ("shift+f8",),
    ("affichage", "mode"): ("lettres",),
    ("affichage", "position"): ("bas_droite",),
    ("affichage", "taille_police"): (20,),
    ("affichage", "opacite"): (0.92,),
    ("affichage", "duree_coches_s"): (30,),
    ("affichage", "comparer_mes_reponses"): (True,),
    ("ia", "backend"): ("auto",),
    ("ia", "modele_reponse"): ("claude-opus-5-5",),
    ("ia", "modele_lecture"): ("claude-opus-5-5",),
    ("ia", "effort_reponse"): ("high",),
    ("ia", "effort_lecture"): ("low",),
    ("ia", "mode_rapide"): (False,),
    ("ia", "cli_commande"): ("claude",),
    ("ia", "cli_modele"): ("",),
    ("ia", "cli_modele_lecture"): ("", "haiku"),
    ("ia", "extraits_par_question"): (8,),
    ("ia", "justifications"): (False,),
    ("ia", "delai_max_s"): (150,),
    ("cours", "dossier"): ("cours",),
    ("cours", "index"): ("index",),
    ("historique", "actif"): (True,),
    ("historique", "dossier"): ("historique",),
    ("historique", "garder_captures"): (False,),
}


def example_config_path(base_dir: Path | None = None) -> Path | None:
    """Fichier d'exemple livré avec le programme."""
    for root in (Path(__file__).resolve().parent.parent, base_dir):
        if root is not None and (root / EXAMPLE_FILENAME).is_file():
            return root / EXAMPLE_FILENAME
    return None


def toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    # Les chaînes TOML de base acceptent les mêmes échappements que JSON.
    return json.dumps(str(value), ensure_ascii=False)


_SECTION = re.compile(r"^\s*\[([^\]]+)\]\s*(#.*)?$")


def _split_value_and_comment(rest: str) -> tuple[str, str]:
    """Sépare « "f8"   # commentaire » en valeur et commentaire (un # entre guillemets n'en est pas un)."""
    quote = None
    escaped = False
    for i, ch in enumerate(rest):
        if quote:
            if escaped:
                escaped = False
            elif ch == "\\" and quote == '"':
                escaped = True
            elif ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch == "#":
            return rest[:i].rstrip(), rest[i:]
    return rest.rstrip(), ""


def set_config_value(text: str, section: str, key: str, value: Any) -> str:
    """Remplace (ou ajoute) ``key = value`` dans ``[section]``, en gardant les commentaires."""
    lines = text.splitlines()
    rendered = toml_value(value)
    key_line = re.compile(rf"^(\s*){re.escape(key)}\s*=(.*)$")
    current = None
    header = None
    end = len(lines)
    for i, line in enumerate(lines):
        match = _SECTION.match(line)
        if match:
            if current == section:
                end = i
                break
            current = match.group(1).strip()
            if current == section:
                header = i
            continue
        if current == section:
            found = key_line.match(line)
            if found:
                _old, comment = _split_value_and_comment(found.group(2))
                new_line = f"{found.group(1)}{key} = {rendered}"
                if comment:
                    column = len(line) - len(comment)  # garde les commentaires alignés
                    new_line += " " * max(1, column - len(new_line)) + comment
                lines[i] = new_line
                return "\n".join(lines) + "\n"
    if header is None:
        lines += ["", f"[{section}]", f"{key} = {rendered}"]
    else:
        insert = end
        while insert > header + 1 and not lines[insert - 1].strip():
            insert -= 1
        lines.insert(insert, f"{key} = {rendered}")
    return "\n".join(lines) + "\n"


def save_setting(base_dir: Path, section: str, key: str, value: Any) -> None:
    """Enregistre un réglage dans config.toml (créé s'il n'existe pas)."""
    path = Path(base_dir) / CONFIG_FILENAME
    text = path.read_text(encoding="utf-8-sig") if path.exists() else ""
    path.write_text(set_config_value(text, section, key, value), encoding="utf-8")


def migrate_config(base_dir: Path) -> str | None:
    """Met config.toml au format courant ; renvoie un message si le fichier a changé.

    Les réglages modifiés par l'étudiant sont conservés ; ceux qui valaient
    encore l'ancienne valeur par défaut prennent la nouvelle. L'ancien fichier
    est gardé sous le nom config.ancien.toml.
    """
    path = Path(base_dir) / CONFIG_FILENAME
    example = example_config_path(base_dir)
    if not path.exists() or example is None:
        return None
    old_text = path.read_text(encoding="utf-8-sig")
    try:
        data = tomllib.loads(old_text)
    except tomllib.TOMLDecodeError:
        return None  # load_config signalera l'erreur à l'étudiant
    if not isinstance(data.get("version", 1), int) or data.get("version", 1) >= CONFIG_VERSION:
        return None
    known = {name: {f.name for f in fields(getattr(Config(), name))} for name in
             ("raccourcis", "affichage", "ia", "cours", "historique")}
    text = example.read_text(encoding="utf-8")
    kept: list[str] = []
    for section, values in data.items():
        if section not in known or not isinstance(values, dict):
            continue
        for key, value in values.items():
            if key not in known[section] or value in LEGACY_DEFAULTS.get((section, key), ()):
                continue
            text = set_config_value(text, section, key, value)
            kept.append(f"{section}.{key}")
    (Path(base_dir) / BACKUP_FILENAME).write_text(old_text, encoding="utf-8")
    path.write_text(text, encoding="utf-8")
    message = "config.toml mis à jour avec les nouveaux réglages par défaut"
    if kept:
        message += f" (réglages personnels conservés : {', '.join(kept)})"
    return message + f" ; l'ancien fichier est gardé dans {BACKUP_FILENAME}"


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
