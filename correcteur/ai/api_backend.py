"""Moteur « API Anthropic » : appelle l'API Messages avec la clé de l'utilisateur.

Deux appels par capture, tous deux avec une sortie structurée (le modèle est
contraint de répondre par un JSON conforme à ``Reading`` ou ``Answers``) :

* ``read``   : transcription de la capture ;
* ``answer`` : correction, à partir de la capture, de la transcription et des
  extraits de cours.

Les appels ne sont pas diffusés en continu (pas de *streaming*) : lorsqu'un
filtre de sécurité interrompt le modèle demandé et qu'un modèle de repli prend
le relais, la réponse non diffusée ne contient que la sortie du modèle de repli,
donc un JSON entier. En diffusion continue, on recevrait le début de l'un suivi
de la fin de l'autre.
"""

from __future__ import annotations

import base64
import dataclasses
import functools
import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Sequence, TypeVar

import anthropic
import httpx2
from pydantic import BaseModel, ValidationError

from ..config import API_KEY_FILENAME, CONFIG_FILENAME, EFFORTS, AIConfig
from ..models import Answers, Excerpt, Reading, ScreenImage
from .base import BackendError
from .prompts import ANSWER_SYSTEM, READ_SYSTEM, answer_user_text, read_user_text

log = logging.getLogger(__name__)

FALLBACK_BETA = "server-side-fallback-2026-07-01"

# La réflexion du modèle compte dans max_tokens, même si son texte n'est pas renvoyé.
MAX_TOKENS = 16_000
MAX_TOKENS_DEEP = 32_000  # efforts « xhigh » et « max » : réflexion nettement plus longue
DEEP_EFFORTS = ("xhigh", "max")

CONNECT_TIMEOUT_S = 15.0
CHECK_TIMEOUT_S = 10.0
MIN_BUDGET_S = 5.0
# Nouveaux essais après une erreur passagère (débit, surcharge, réseau). On ne
# s'appuie pas sur ceux du SDK : chacun repartirait avec le délai complet, et
# une capture pourrait attendre trois fois ``delai_max_s``.
MAX_TRANSIENT_RETRIES = 2
MIN_RETRY_BUDGET_S = 20.0  # en dessous, un nouvel essai n'a aucune chance d'aboutir
RETRY_WAITS_S = (2.0, 4.0)
DEFAULT_RATE_LIMIT_WAIT_S = 5.0

ModelT = TypeVar("ModelT", bound=BaseModel)


# --------------------------------------------------------------------------
# Règles d'envoi par modèle
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ModelRules:
    """Ce qu'un modèle accepte dans une requête."""

    adaptive_thinking: bool  # envoyer thinking={"type": "adaptive"}
    efforts: tuple[str, ...]  # niveaux admis par output_config.effort ; vide = ne pas l'envoyer
    fallback: bool = False  # repli côté serveur quand un filtre de sécurité refuse la requête
    fast: bool = False  # speed="fast" accepté sans en-tête bêta ni accès particulier


_ALL_EFFORTS = EFFORTS
_NO_XHIGH = ("low", "medium", "high", "max")

MODEL_RULES: dict[str, ModelRules] = {
    "claude-opus-5-5": ModelRules(True, _ALL_EFFORTS, fallback=True, fast=True),
    # Mode rapide d'Opus 5 : aperçu sur liste d'attente, avec en-tête bêta. Non envoyé.
    "claude-opus-5": ModelRules(True, _ALL_EFFORTS, fallback=True),
    "claude-sonnet-5-5": ModelRules(True, _ALL_EFFORTS, fallback=True),
    "claude-fable-5-2": ModelRules(True, _ALL_EFFORTS, fallback=True, fast=True),
    "claude-fable-5-1": ModelRules(True, _ALL_EFFORTS, fallback=True),
    "claude-fable-5": ModelRules(True, _ALL_EFFORTS, fallback=True),
    # Haiku 5.5 n'a pas de repli côté serveur : une liste de replis y est rejetée.
    "claude-haiku-5-5": ModelRules(True, _ALL_EFFORTS),
    "claude-sonnet-5": ModelRules(True, _ALL_EFFORTS),
    "claude-opus-4-8": ModelRules(True, _ALL_EFFORTS),
    "claude-opus-4-7": ModelRules(True, _ALL_EFFORTS),
    "claude-opus-4-6": ModelRules(True, _NO_XHIGH),
    "claude-sonnet-4-6": ModelRules(True, _NO_XHIGH),
    # Générations précédentes : la réflexion y passe par un budget de jetons, que
    # l'on n'envoie jamais ; ces modèles répondent donc sans réflexion.
    "claude-opus-4-5": ModelRules(False, ("low", "medium", "high")),
    "claude-sonnet-4-5": ModelRules(False, ()),
    "claude-haiku-4-5": ModelRules(False, ()),
}

# Identifiant inconnu (modèle futur, faute de frappe...) : on n'envoie que ce que
# tous les modèles acceptent. Sans champ ``thinking`` ni ``effort``, les modèles
# récents appliquent d'eux-mêmes leur réflexion adaptative et leur effort par défaut.
DEFAULT_RULES = ModelRules(adaptive_thinking=False, efforts=())

_DATED_ID = re.compile(r"(.+)-\d{8}")


def _normalize(model: str) -> str:
    return (model or "").strip().lower()


def rules_for(model: str) -> ModelRules:
    """Règles d'envoi d'un modèle ; ``DEFAULT_RULES`` si l'identifiant est inconnu."""
    key = _normalize(model)
    if key in MODEL_RULES:
        return MODEL_RULES[key]
    dated = _DATED_ID.fullmatch(key)  # ex. claude-haiku-4-5-20251001
    if dated and dated.group(1) in MODEL_RULES:
        return MODEL_RULES[dated.group(1)]
    return DEFAULT_RULES


def effective_effort(rules: ModelRules, effort: str) -> str | None:
    """Niveau d'effort à envoyer, ou ``None`` s'il ne faut pas en envoyer.

    Un niveau que le modèle n'accepte pas est ramené au niveau admis juste en dessous.
    """
    if not rules.efforts or effort not in EFFORTS:
        return None
    if effort in rules.efforts:
        return effort
    for level in reversed(EFFORTS[: EFFORTS.index(effort)]):
        if level in rules.efforts:
            return level
    return rules.efforts[0]


@functools.lru_cache(maxsize=None)
def _schema(output_type: type[BaseModel]) -> dict[str, Any]:
    return anthropic.transform_schema(output_type)


def build_request(
    *,
    model: str,
    effort: str,
    system: str,
    content: list[dict[str, Any]],
    output_type: type[BaseModel],
    fallback: bool = True,
    fast: bool = False,
) -> dict[str, Any]:
    """Arguments de ``messages.create`` pour un appel.

    ``fallback`` et ``fast`` disent ce que l'on souhaite ; ils ne sont envoyés
    que si le modèle les accepte. Les clés ``betas`` et ``fallbacks`` ne sont
    présentes que lorsque le repli est demandé : l'appel passe alors par
    ``client.beta.messages``.
    """
    rules = rules_for(model)
    output_config: dict[str, Any] = {"format": {"type": "json_schema", "schema": _schema(output_type)}}
    level = effective_effort(rules, effort)
    if level is not None:
        output_config["effort"] = level
    params: dict[str, Any] = {
        "model": model,
        "max_tokens": MAX_TOKENS_DEEP if level in DEEP_EFFORTS else MAX_TOKENS,
        "system": system,
        "messages": [{"role": "user", "content": content}],
        "output_config": output_config,
    }
    if rules.adaptive_thinking:
        params["thinking"] = {"type": "adaptive"}
    if fast and rules.fast:
        params["speed"] = "fast"
    if fallback and rules.fallback:
        params["betas"] = [FALLBACK_BETA]
        params["fallbacks"] = "default"
    return params


# --------------------------------------------------------------------------
# Petits outils
# --------------------------------------------------------------------------

_IMAGE_SIGNATURES = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF8", "image/gif"),
)


def _media_type(data: bytes) -> str:
    for signature, media_type in _IMAGE_SIGNATURES:
        if data.startswith(signature):
            return media_type
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/png"


def _image_content(image: ScreenImage, text: str) -> list[dict[str, Any]]:
    if not image.png:
        raise BackendError("Capture d'écran vide. Réessaie.", detail="ScreenImage.png est vide")
    return [
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": _media_type(image.png),
                "data": base64.standard_b64encode(image.png).decode("ascii"),
            },
        },
        {"type": "text", "text": text},
    ]


def _has_credentials(client: Any) -> bool:
    return bool(
        getattr(client, "api_key", None)
        or getattr(client, "auth_token", None)
        or getattr(client, "credentials", None) is not None
    )


def _retry_after_s(exc: anthropic.APIStatusError) -> float | None:
    """Attente demandée par le serveur, en secondes, si elle est indiquée."""
    headers = exc.response.headers
    for name, scale in (("retry-after-ms", 0.001), ("retry-after", 1.0)):
        raw = headers.get(name)
        if raw is None:
            continue
        try:
            value = float(raw) * scale
        except ValueError:
            continue  # une date HTTP : cas rare, on applique l'attente par défaut
        if value >= 0:
            return value
    return None


def _is_billing(exc: anthropic.APIStatusError) -> bool:
    if exc.status_code == 402 or exc.type == "billing_error":
        return True
    # Un compte sans crédit reçoit aussi un 400 ordinaire, que seul son texte distingue.
    return "credit balance" in (exc.api_message or "").lower()


def _describe(exc: BaseException) -> str:
    """Résumé technique d'une erreur, pour le journal."""
    parts = [type(exc).__name__]
    status = getattr(exc, "status_code", None)
    if status:
        parts.append(f"HTTP {status}")
    for attribute in ("type", "error_code"):
        value = getattr(exc, attribute, None)
        if value:
            parts.append(str(value))
    request_id = getattr(exc, "request_id", None)
    if request_id:
        parts.append(f"request_id={request_id}")
    parts.append((getattr(exc, "api_message", None) or str(exc))[:400])
    if exc.__cause__ is not None:
        parts.append(f"cause : {type(exc.__cause__).__name__}: {exc.__cause__}"[:200])
    return " | ".join(parts)


def _final_text(message: Any) -> str:
    """Texte de la réponse, sans la réflexion.

    Si un modèle de repli a pris le relais, seul ce qui suit le dernier
    marqueur de repli constitue la réponse.
    """
    texts: list[str] = []
    for block in getattr(message, "content", None) or []:
        kind = getattr(block, "type", None)
        if kind == "fallback":
            texts.clear()
        elif kind == "text":
            texts.append(getattr(block, "text", "") or "")
    return "".join(texts).strip()


def _usage_summary(message: Any) -> str:
    usage = getattr(message, "usage", None)
    if usage is None:
        return "consommation inconnue"
    parts = [
        f"{getattr(usage, 'input_tokens', 0) or 0} jetons en entrée",
        f"{getattr(usage, 'output_tokens', 0) or 0} en sortie",
    ]
    thinking = getattr(getattr(usage, "output_tokens_details", None), "thinking_tokens", None)
    if thinking:
        parts.append(f"dont {thinking} de réflexion")
    for label, attribute in (
        ("lus dans le cache", "cache_read_input_tokens"),
        ("écrits dans le cache", "cache_creation_input_tokens"),
    ):
        value = getattr(usage, attribute, None)
        if value:
            parts.append(f"{value} {label}")
    if getattr(usage, "speed", None) == "fast":
        parts.append("mode rapide")
    # Avec un repli, le total ci-dessus ne couvre que la tentative qui a répondu.
    iterations = getattr(usage, "iterations", None) or []
    if len(iterations) > 1:
        attempts = [
            f"{getattr(it, 'model', None) or '?'} "
            f"{getattr(it, 'input_tokens', 0)}+{getattr(it, 'output_tokens', 0)}"
            for it in iterations
        ]
        parts.append("tentatives : " + ", ".join(attempts))
    return ", ".join(parts)


def _served_by_fallback(message: Any) -> bool:
    """Vrai si un modèle de repli a produit la réponse.

    On ne compare pas le nom du modèle renvoyé à celui demandé : l'API peut
    renvoyer un identifiant daté pour un alias.
    """
    iterations = getattr(getattr(message, "usage", None), "iterations", None) or []
    if any(getattr(it, "type", None) == "fallback_message" for it in iterations):
        return True
    return any(getattr(block, "type", None) == "fallback" for block in getattr(message, "content", None) or [])


@dataclass(frozen=True)
class _Call:
    """Un appel au modèle : lecture ou correction."""

    label: str  # « lecture » ou « correction », pour le journal
    model: str
    model_key: str  # nom du réglage dans config.toml, cité dans les messages d'erreur
    effort: str
    effort_key: str
    system: str
    output_type: type[BaseModel]


@dataclass(frozen=True)
class _Extras:
    """Options facultatives d'une requête, que l'on peut retirer si l'API les rejette."""

    fallback: bool
    fast: bool


class _Refusal(BackendError):
    """Le modèle (ou un filtre de sécurité) a refusé de répondre."""


# --------------------------------------------------------------------------
# Moteur
# --------------------------------------------------------------------------


class ApiBackend:
    """Moteur qui appelle l'API Anthropic. Utilisable depuis plusieurs fils à la fois."""

    name = "api"

    def __init__(self, cfg: AIConfig, client: anthropic.Anthropic | None = None) -> None:
        self._cfg = cfg
        self._client = client
        self._owns_client = client is None
        self._lock = threading.Lock()
        self._degraded: dict[str, _Extras] = {}  # modèle -> options qui passent réellement
        self._warned_unknown: set[str] = set()
        # Remplaçables dans les tests pour ne pas attendre réellement.
        self._clock = time.monotonic
        self._sleep = time.sleep

    # -- interface Backend ---------------------------------------------------

    def read(self, image: ScreenImage) -> Reading:
        cfg = self._cfg
        deadline = self._deadline()
        content = _image_content(image, read_user_text(image))
        call = _Call(
            label="lecture",
            model=cfg.modele_lecture,
            model_key="modele_lecture",
            effort=cfg.effort_lecture,
            effort_key="effort_lecture",
            system=READ_SYSTEM,
            output_type=Reading,
        )
        try:
            return self._run(call, content, deadline)
        except _Refusal as refusal:
            same_model = _normalize(cfg.modele_reponse) == _normalize(cfg.modele_lecture)
            if same_model or deadline - self._clock() < MIN_RETRY_BUDGET_S:
                raise
            log.warning(
                "API lecture : refus de %s (%s) ; nouvel essai avec %s",
                cfg.modele_lecture,
                refusal.detail,
                cfg.modele_reponse,
            )
            retry = dataclasses.replace(call, model=cfg.modele_reponse, model_key="modele_reponse")
            return self._run(retry, content, deadline)

    def answer(
        self,
        image: ScreenImage,
        reading: Reading | None,
        excerpts: Sequence[Excerpt],
    ) -> Answers:
        cfg = self._cfg
        deadline = self._deadline()
        text = answer_user_text(reading, excerpts, justifications=cfg.justifications)
        content = _image_content(image, text)
        call = _Call(
            label="correction",
            model=cfg.modele_reponse,
            model_key="modele_reponse",
            effort=cfg.effort_reponse,
            effort_key="effort_reponse",
            system=ANSWER_SYSTEM,
            output_type=Answers,
        )
        return self._run(call, content, deadline)

    def check(self) -> str:
        cfg = self._cfg
        client = self._ready_client()
        targets = [(cfg.modele_reponse, "modele_reponse", "correction")]
        if _normalize(cfg.modele_lecture) != _normalize(cfg.modele_reponse):
            targets.append((cfg.modele_lecture, "modele_lecture", "lecture"))
        timeout_s = min(CHECK_TIMEOUT_S, self._budget_s())
        scoped = client.with_options(timeout=timeout_s, max_retries=1)
        no_answer = "Anthropic ne répond pas. Vérifie ta connexion Internet."
        remarks: list[str] = []
        for model, key, _role in targets:
            if not _normalize(model):
                raise BackendError(f"{key} est vide dans {CONFIG_FILENAME}.", detail=f"{key} vide")
            try:
                info = scoped.models.retrieve(model.strip())
            except Exception as exc:
                raise self._translate(exc, model=model, model_key=key, timeout_message=no_answer) from exc
            self._check_model(info, model, key)
            if getattr(info, "lifecycle", None) == "deprecated":
                remarks.append(f"{model} sera bientôt retiré par Anthropic")
            if rules_for(model) is DEFAULT_RULES:
                remarks.append(f"{model} n'est pas connu de l'application : réglages d'effort ignorés")
        if cfg.mode_rapide and not any(rules_for(model).fast for model, _key, _role in targets):
            remarks.append("mode rapide indisponible avec ces modèles")
        if len(targets) == 1:
            status = f"API Anthropic : clé valide, modèle {targets[0][0]} disponible"
        else:
            listing = " et ".join(f"{model} ({role})" for model, _key, role in targets)
            status = f"API Anthropic : clé valide, modèles {listing} disponibles"
        if remarks:
            status += " (" + " ; ".join(remarks) + ")"
        return status + "."

    # -- déroulement d'un appel ----------------------------------------------

    def _budget_s(self) -> float:
        return max(MIN_BUDGET_S, float(self._cfg.delai_max_s))

    def _deadline(self) -> float:
        return self._clock() + self._budget_s()

    def _run(self, call: _Call, content: list[dict[str, Any]], deadline: float) -> Any:
        client = self._ready_client()
        started = self._clock()
        message = self._send(client, call, content, deadline)
        if not hasattr(message, "stop_reason") or not hasattr(message, "content"):
            # Une page HTML à la place de la réponse : portail Wi-Fi, proxy, antivirus...
            raise self._fail(
                call,
                "Réponse inattendue du serveur (connexion filtrée ?). Vérifie ta connexion Internet.",
                f"réponse de type {type(message).__name__} : {str(message)[:200]}",
            )
        log.info(
            "API %s : modèle %s%s, %s, %.1f s, arrêt « %s », requête %s",
            call.label,
            getattr(message, "model", None) or call.model,
            f" (en repli de {call.model})" if _served_by_fallback(message) else "",
            _usage_summary(message),
            self._clock() - started,
            message.stop_reason,
            getattr(message, "_request_id", None),
        )
        return self._interpret(call, message)

    def _interpret(self, call: _Call, message: Any) -> Any:
        """Vérifie pourquoi le modèle s'est arrêté, puis valide le JSON."""
        request_id = getattr(message, "_request_id", None)
        if message.stop_reason == "refusal":
            details = getattr(message, "stop_details", None)
            detail = (
                f"refus du modèle {getattr(message, 'model', call.model)} ; "
                f"catégorie={getattr(details, 'category', None)} ; "
                f"explication={getattr(details, 'explanation', None)} ; "
                f"modèle conseillé={getattr(details, 'recommended_model', None)} ; requête {request_id}"
            )
            log.warning("API %s : %s", call.label, detail)
            raise _Refusal(
                "Le modèle a refusé cette capture (filtre de sécurité). "
                "Réessaie avec moins de questions à l'écran.",
                detail=detail,
            )
        if message.stop_reason in ("max_tokens", "model_context_window_exceeded"):
            raise self._fail(
                call,
                "Réponse trop longue, coupée avant la fin. Capture moins de questions à la fois "
                f"(ou baisse {call.effort_key} dans {CONFIG_FILENAME}).",
                f"stop_reason={message.stop_reason} ; requête {request_id}",
            )
        text = _final_text(message)
        candidates = [text]
        start, end = text.find("{"), text.rfind("}")
        if 0 <= start < end and (start, end) != (0, len(text) - 1):
            candidates.append(text[start : end + 1])  # JSON entouré de texte ou de ```
        error: Exception | None = None
        for candidate in candidates:
            try:
                return call.output_type.model_validate_json(candidate)
            except ValidationError as exc:
                error = error or exc
        raise self._fail(
            call,
            "Réponse du modèle illisible. Réessaie.",
            f"stop_reason={message.stop_reason} ; requête {request_id} ; "
            f"{str(error)[:300]} ; début du texte : {text[:200]!r}",
        )

    def _send(
        self,
        client: anthropic.Anthropic,
        call: _Call,
        content: list[dict[str, Any]],
        deadline: float,
    ) -> Any:
        """Envoie la requête, avec nouveaux essais dans la limite du délai imparti."""
        rules = rules_for(call.model)
        if rules is DEFAULT_RULES:
            self._warn_unknown(call)
        steps = self._steps(call.model, rules)
        step = 0
        proven: int | None = None  # dernier palier atteint à cause d'un rejet 400
        retries_left = MAX_TRANSIENT_RETRIES
        while True:
            remaining = deadline - self._clock()
            if remaining <= 0:
                raise self._fail(call, self._timeout_message(), "délai épuisé avant l'envoi")
            params = build_request(
                model=call.model,
                effort=call.effort,
                system=call.system,
                content=content,
                output_type=call.output_type,
                fallback=steps[step].fallback,
                fast=steps[step].fast,
            )
            wait: float
            try:
                message = self._post(client, params, remaining)
            except anthropic.BadRequestError as exc:
                # L'en-tête bêta du repli ou le mode rapide peuvent ne pas être
                # ouverts à ce compte : on renvoie la même requête sans eux.
                if step + 1 < len(steps) and not _is_billing(exc):
                    log.warning(
                        "API %s : requête rejetée avec %s (%s) ; nouvel essai sans",
                        call.label,
                        "le mode rapide" if steps[step].fast else "le repli en cas de refus",
                        _describe(exc),
                    )
                    step += 1
                    proven = step
                    continue
                raise self._translate(exc, model=call.model, model_key=call.model_key) from exc
            except anthropic.RateLimitError as exc:
                if isinstance(exc, anthropic.EnforcedSpendLimitReachedError):
                    raise self._translate(exc, model=call.model, model_key=call.model_key) from exc
                if steps[step].fast:
                    # Le mode rapide a sa propre limite de débit : on repasse en vitesse normale.
                    log.warning(
                        "API %s : limite de débit du mode rapide ; nouvel essai en vitesse normale",
                        call.label,
                    )
                    step += 1
                    continue
                failure: Exception = exc
                requested = _retry_after_s(exc)
                wait = DEFAULT_RATE_LIMIT_WAIT_S if requested is None else requested
            except (anthropic.InternalServerError, anthropic.APIConnectionError) as exc:
                failure = exc
                wait = RETRY_WAITS_S[min(MAX_TRANSIENT_RETRIES - retries_left, len(RETRY_WAITS_S) - 1)]
            except Exception as exc:
                raise self._translate(exc, model=call.model, model_key=call.model_key) from exc
            else:
                if proven is not None:
                    with self._lock:
                        self._degraded[_normalize(call.model)] = steps[proven]
                return message

            remaining = deadline - self._clock()
            if retries_left <= 0 or remaining - wait < MIN_RETRY_BUDGET_S:
                raise self._translate(failure, model=call.model, model_key=call.model_key) from failure
            retries_left -= 1
            log.warning("API %s : %s ; nouvel essai dans %.0f s", call.label, _describe(failure), wait)
            self._sleep(wait)

    def _post(self, client: anthropic.Anthropic, params: dict[str, Any], timeout_s: float) -> Any:
        scoped = client.with_options(
            timeout=anthropic.Timeout(timeout_s, connect=min(CONNECT_TIMEOUT_S, timeout_s)),
            max_retries=0,
        )
        if "fallbacks" in params:
            return scoped.beta.messages.create(**params)
        return scoped.messages.create(**params)

    def _steps(self, model: str, rules: ModelRules) -> list[_Extras]:
        """Options à essayer, de la requête complète à la requête nue."""
        full = _Extras(fallback=rules.fallback, fast=rules.fast and bool(self._cfg.mode_rapide))
        steps = [full]
        if full.fast:
            steps.append(_Extras(fallback=full.fallback, fast=False))
        if full.fallback:
            steps.append(_Extras(fallback=False, fast=False))
        with self._lock:
            known = self._degraded.get(_normalize(model))
        if known in steps:
            steps = steps[steps.index(known) :]
        return steps

    def _warn_unknown(self, call: _Call) -> None:
        key = _normalize(call.model)
        with self._lock:
            if key in self._warned_unknown:
                return
            self._warned_unknown.add(key)
        log.warning(
            "Modèle « %s » (%s) inconnu de l'application : requête envoyée sans réglage d'effort, "
            "sans repli en cas de refus et sans mode rapide",
            call.model,
            call.model_key,
        )

    # -- client et erreurs ---------------------------------------------------

    def _ready_client(self) -> anthropic.Anthropic:
        """Client prêt à l'emploi, ou erreur claire si aucune clé n'est disponible."""
        with self._lock:
            client = self._client
            # La clé est lue à la création du client : on le recrée tant qu'elle
            # manque, pour qu'une clé ajoutée entre-temps soit prise en compte.
            if self._owns_client and (client is None or not _has_credentials(client)):
                if client is not None:
                    client.close()
                try:
                    client = anthropic.Anthropic(max_retries=0)
                except Exception as exc:
                    self._client = None
                    raise self._missing_key(_describe(exc)) from exc
                self._client = client
        if not _has_credentials(client):
            raise self._missing_key("aucune clé (ANTHROPIC_API_KEY) dans l'environnement")
        return client

    def _missing_key(self, detail: str) -> BackendError:
        return BackendError(
            f"Clé API absente. Colle ta clé Anthropic dans le fichier {API_KEY_FILENAME}, puis relance.",
            detail=self._scrub(detail),
        )

    def _timeout_message(self) -> str:
        return (
            f"Pas de réponse en {self._budget_s():.0f} s. "
            f"Réessaie (ou augmente delai_max_s dans {CONFIG_FILENAME})."
        )

    def _fail(self, call: _Call, message: str, detail: str) -> BackendError:
        detail = self._scrub(detail)
        log.warning("API %s : %s | %s", call.label, message, detail)
        return BackendError(message, detail=detail)

    def _scrub(self, text: str) -> str:
        """Retire la clé d'un texte destiné au journal, par précaution."""
        client = self._client
        secrets = {
            getattr(client, "api_key", None),
            getattr(client, "auth_token", None),
            os.environ.get("ANTHROPIC_API_KEY"),
        }
        for secret in secrets:
            if isinstance(secret, str) and len(secret.strip()) >= 8:
                text = text.replace(secret.strip(), "[clé masquée]")
        return text

    def _translate(
        self,
        exc: BaseException,
        *,
        model: str,
        model_key: str,
        timeout_message: str | None = None,
    ) -> BackendError:
        """Traduit une erreur du SDK en message court. Du cas le plus précis au plus général."""
        if isinstance(exc, anthropic.AuthenticationError):
            message = f"Clé API refusée par Anthropic. Vérifie la clé collée dans {API_KEY_FILENAME}."
        elif isinstance(exc, anthropic.PermissionDeniedError):
            message = "Accès refusé par Anthropic pour cette clé (compte ou pays non autorisé)."
        elif isinstance(exc, anthropic.NotFoundError):
            message = (
                f"Modèle « {model} » inconnu ou indisponible pour ta clé. "
                f"Corrige {model_key} dans {CONFIG_FILENAME}."
            )
        elif isinstance(exc, anthropic.ContentTooLargeError):
            message = "Capture trop lourde pour l'API. Réessaie avec une fenêtre plus petite."
        elif isinstance(exc, anthropic.EnforcedSpendLimitReachedError):
            message = "Plafond de dépense mensuel atteint sur ton compte Anthropic."
        elif isinstance(exc, anthropic.RateLimitError):
            wait = _retry_after_s(exc)
            delay = f"{wait:.0f} s" if wait and wait >= 1 else "une minute"
            message = f"Trop de demandes pour ta clé API. Attends {delay} puis réessaie."
        elif isinstance(exc, anthropic.APIStatusError) and _is_billing(exc):
            message = (
                "Crédit API épuisé. Ajoute du crédit sur la console Anthropic "
                "(platform.claude.com, rubrique Billing)."
            )
        elif isinstance(exc, anthropic.BadRequestError):
            message = (
                f"Requête refusée par l'API pour le modèle « {model} ». "
                f"Vérifie la section [ia] de {CONFIG_FILENAME}."
            )
        elif isinstance(exc, anthropic.InternalServerError):
            message = "Les serveurs d'Anthropic sont surchargés ou en panne. Réessaie dans un instant."
        elif isinstance(exc, anthropic.APIStatusError):
            message = f"Erreur {exc.status_code} renvoyée par l'API Anthropic. Réessaie dans un instant."
        elif isinstance(exc, anthropic.APITimeoutError) and not isinstance(
            exc.__cause__, httpx2.ConnectTimeout
        ):
            message = timeout_message or self._timeout_message()
        elif isinstance(exc, anthropic.APIConnectionError):
            # Y compris le serveur injoignable dans le temps de connexion imparti.
            message = "Connexion à Anthropic impossible. Vérifie ta connexion Internet."
        elif isinstance(exc, anthropic.CredentialsError):
            message = f"Clé API absente ou illisible. Colle ta clé Anthropic dans {API_KEY_FILENAME}."
        elif isinstance(exc, anthropic.AnthropicError):
            message = "Erreur de l'API Anthropic. Réessaie dans un instant."
        else:
            # Corps de réponse qui n'est pas du JSON, erreur imprévue du SDK...
            message = "Erreur inattendue pendant l'appel à l'API. Réessaie."
        detail = self._scrub(_describe(exc))
        log.warning("API (%s = %s) : %s | %s", model_key, model, message, detail)
        return BackendError(message, detail=detail)

    def _check_model(self, info: Any, model: str, model_key: str) -> None:
        """Refuse un modèle retiré, ou incapable de lire une image ou de répondre en JSON."""
        advice = f"Choisis-en un autre ({model_key} dans {CONFIG_FILENAME})."
        if getattr(info, "lifecycle", None) == "retired":
            raise BackendError(
                f"Le modèle « {model} » a été retiré par Anthropic. {advice}", detail="lifecycle=retired"
            )
        capabilities = getattr(info, "capabilities", None)
        for attribute, problem in (
            ("image_input", "ne lit pas les images"),
            ("structured_outputs", "ne sait pas répondre au format attendu"),
        ):
            if getattr(getattr(capabilities, attribute, None), "supported", None) is False:
                raise BackendError(
                    f"Le modèle « {model} » {problem}. {advice}", detail=f"{attribute} non pris en charge"
                )
