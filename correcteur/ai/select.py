"""Choix du moteur : clé API Anthropic, ou abonnement Claude via Claude Code."""

from __future__ import annotations

from ..config import Config, load_api_key
from .base import Backend, BackendError
from .cli_backend import CliBackend, find_executable

NO_BACKEND_HELP = (
    "Aucune connexion à Claude n'est configurée. Deux possibilités : mettre une clé API Anthropic "
    "dans le fichier cle_api.txt, ou installer Claude Code et s'y connecter avec son abonnement "
    "(voir le README)."
)
NO_KEY_HELP = (
    "Aucune clé API trouvée : colle ta clé Anthropic dans le fichier cle_api.txt du dossier du correcteur."
)


def make_backend(cfg: Config) -> Backend:
    """Construit le moteur demandé par ``cfg.ia.backend`` ; lève ``BackendError`` si c'est impossible."""
    choice = cfg.ia.backend
    if choice == "claude_cli":
        return CliBackend(cfg.ia)
    key = load_api_key(cfg.base_dir)
    if choice == "api" or (choice == "auto" and key):
        if not key:
            raise BackendError(NO_KEY_HELP)
        from .api_backend import ApiBackend  # import tardif : le SDK n'est utile qu'avec une clé

        return ApiBackend(cfg.ia)
    if find_executable(cfg.ia.cli_commande):
        return CliBackend(cfg.ia)
    raise BackendError(NO_BACKEND_HELP)
