"""Tests du moteur API.

Aucun appel réseau : le vrai client ``anthropic.Anthropic`` est construit avec
une clé factice et un transport HTTP factice. Chaque test voit donc la requête
telle que le SDK l'aurait envoyée (chemin, en-têtes, corps JSON) et répond par
une réponse de l'API Messages préparée à l'avance.
"""

from __future__ import annotations

import base64
import io
import json
import logging
import os
import threading
from dataclasses import dataclass
from typing import Any, Callable

import anthropic
import httpx2
import pytest
from anthropic import Anthropic, DefaultHttpxClient
from PIL import Image

from correcteur.ai import api_backend
from correcteur.ai.api_backend import (
    DEFAULT_RULES,
    FALLBACK_BETA,
    MAX_TOKENS,
    MAX_TOKENS_DEEP,
    ApiBackend,
    build_request,
    effective_effort,
    rules_for,
)
from correcteur.ai.base import Backend, BackendError
from correcteur.ai.prompts import ANSWER_SYSTEM, READ_SYSTEM, answer_user_text, read_user_text
from correcteur.config import AIConfig
from correcteur.models import (
    AnsweredQuestion,
    Answers,
    Excerpt,
    Reading,
    ReadProposition,
    ReadQuestion,
    ScreenImage,
    Verdict,
)
from correcteur.pipeline import correct_screen

FAKE_KEY = "sk-ant-api03-cle-factice-0123456789abcdef"
LOGGER = "correcteur.ai.api_backend"
# Paramètres que l'on ne doit jamais envoyer, quel que soit le modèle.
FORBIDDEN = {"temperature", "top_p", "top_k", "budget_tokens", "stream", "tools", "tool_choice"}


def _png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (64, 40), "white").save(buffer, "PNG")
    return buffer.getvalue()


IMAGE = ScreenImage(png=_png(), width=1280, height=800)

READING = Reading(
    questions=[
        ReadQuestion(
            numero="1",
            debut_visible=True,
            type="QRM",
            enonce="Concernant les récepteurs couplés aux protéines G :",
            propositions=[
                ReadProposition(lettre=letter, texte=f"Proposition {letter}", cochee=letter == "B", x=120, y=y)
                for letter, y in zip("ABCDE", (200, 250, 300, 350, 400))
            ],
            complete=True,
            requetes=["récepteur couplé protéine G", "adénylate cyclase AMPc"],
        )
    ]
)
ANSWERS = Answers(
    questions=[
        AnsweredQuestion(
            numero="1",
            cocher="vraies",
            verdicts=[Verdict(lettre=letter, vraie=letter in "ACD") for letter in "ABCDE"],
            reponse_libre="",
            source="cours",
            confiance="haute",
            note="",
        )
    ]
)
EXCERPTS = [
    Excerpt(source="Biochimie 3.pdf", page=12, text="La protéine Gs active l'adénylate cyclase.", chunk_id=7),
    Excerpt(source="Annale 2023.pdf", page=0, text="Q1 : réponses ACD.", chunk_id=9),
]


# --------------------------------------------------------------------------
# API factice
# --------------------------------------------------------------------------


@dataclass
class Captured:
    method: str
    path: str
    headers: dict[str, str]
    body: Any
    timeout: dict[str, float]


Reply = Callable[[httpx2.Request], httpx2.Response]


class FakeApi:
    """Transport HTTP factice : enregistre les requêtes et rejoue des réponses.

    Les réponses sont servies dans l'ordre ; la dernière est répétée sans fin.
    """

    def __init__(self, *replies: Reply) -> None:
        self.replies = list(replies)
        self.requests: list[Captured] = []
        self._lock = threading.Lock()

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content) if request.content else None
        with self._lock:
            self.requests.append(
                Captured(
                    method=request.method,
                    path=request.url.path,
                    headers=dict(request.headers),
                    body=body,
                    timeout=dict(request.extensions.get("timeout") or {}),
                )
            )
            reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        return reply(request)

    @property
    def bodies(self) -> list[dict[str, Any]]:
        return [r.body for r in self.requests]


class FakeClock:
    """Horloge que seuls les tests font avancer (attentes et délais simulés)."""

    def __init__(self) -> None:
        self.now = 1_000.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def message_body(
    text: str | None = None,
    *,
    model: str = "claude-opus-5-5",
    stop_reason: str = "end_turn",
    content: list[dict[str, Any]] | None = None,
    stop_details: dict[str, Any] | None = None,
    usage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Réponse de l'API Messages, avec le bloc de réflexion vide que renvoient les modèles récents."""
    if content is None:
        content = [{"type": "thinking", "thinking": "", "signature": "c2lnbmF0dXJl"}]
        if text is not None:
            content.append({"type": "text", "text": text})
    return {
        "id": "msg_01Factice",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "stop_details": stop_details,
        "usage": usage
        or {
            "input_tokens": 6200,
            "output_tokens": 1800,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
            "output_tokens_details": {"thinking_tokens": 400},
        },
    }


def ok(text: str | None = None, **kwargs: Any) -> Reply:
    def reply(request: httpx2.Request) -> httpx2.Response:
        requested = json.loads(request.content)["model"]
        body = message_body(text, **{"model": requested, **kwargs})
        return httpx2.Response(200, json=body, headers={"request-id": "req_factice"})

    return reply


def refusal(category: str | None = "bio", **kwargs: Any) -> Reply:
    details = {"type": "refusal", "category": category, "explanation": "Contenu de biologie sensible."}
    return ok(None, stop_reason="refusal", content=[], stop_details=details, **kwargs)


def api_error(
    status: int,
    error_type: str,
    message: str,
    *,
    headers: dict[str, str] | None = None,
    details: dict[str, Any] | None = None,
) -> Reply:
    error: dict[str, Any] = {"type": error_type, "message": message}
    if details:
        error["details"] = details
    payload = {"type": "error", "error": error, "request_id": "req_erreur"}

    def reply(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(status, json=payload, headers={"request-id": "req_erreur", **(headers or {})})

    return reply


def by_system(read: Reply, answer: Reply) -> Reply:
    """Répond selon la phase, reconnue à la consigne système (les deux appels peuvent être simultanés)."""

    def reply(request: httpx2.Request) -> httpx2.Response:
        system = json.loads(request.content)["system"]
        return read(request) if system == READ_SYSTEM else answer(request)

    return reply


def by_model(**replies: Reply) -> Reply:
    def reply(request: httpx2.Request) -> httpx2.Response:
        model = json.loads(request.content)["model"]
        return replies[model.replace("-", "_")](request)

    return reply


def make_client(fake: FakeApi) -> Anthropic:
    return Anthropic(api_key=FAKE_KEY, http_client=DefaultHttpxClient(transport=httpx2.MockTransport(fake)))


def make_backend(fake: FakeApi, **settings: Any) -> tuple[ApiBackend, FakeClock]:
    backend = ApiBackend(AIConfig(**settings), client=make_client(fake))
    clock = FakeClock()
    backend._clock = clock
    backend._sleep = clock.sleep
    return backend, clock


def assert_clean(body: dict[str, Any]) -> None:
    """Aucun paramètre interdit, et un seul message, de l'utilisateur (pas d'amorce de réponse)."""
    assert not FORBIDDEN & set(body)
    assert "budget_tokens" not in json.dumps(body)
    assert [m["role"] for m in body["messages"]] == ["user"]


def expect_error(call: Callable[[], Any]) -> BackendError:
    with pytest.raises(BackendError) as caught:
        call()
    error = caught.value
    assert FAKE_KEY not in str(error)
    assert FAKE_KEY not in error.detail
    return error


# --------------------------------------------------------------------------
# Corps des requêtes
# --------------------------------------------------------------------------


def test_backend_satisfies_the_protocol():
    backend, _ = make_backend(FakeApi(ok(READING.model_dump_json())))
    assert isinstance(backend, Backend)
    assert backend.name == "api"


def test_read_request_for_opus_5_5():
    fake = FakeApi(ok(READING.model_dump_json()))
    backend, _ = make_backend(fake)

    assert backend.read(IMAGE) == READING

    [request] = fake.requests
    assert (request.method, request.path) == ("POST", "/v1/messages")
    assert request.headers["anthropic-beta"] == FALLBACK_BETA
    assert request.headers["authorization"] == f"Bearer {FAKE_KEY}"
    body = request.body
    assert_clean(body)
    assert set(body) == {"model", "max_tokens", "system", "messages", "output_config", "thinking", "fallbacks"}
    assert body["model"] == "claude-opus-5-5"
    assert body["max_tokens"] == MAX_TOKENS
    assert body["system"] == READ_SYSTEM
    assert body["thinking"] == {"type": "adaptive"}
    assert body["fallbacks"] == "default"
    assert body["output_config"] == {
        "effort": "low",
        "format": {"type": "json_schema", "schema": anthropic.transform_schema(Reading)},
    }
    image_block, text_block = body["messages"][0]["content"]
    assert image_block == {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/png",
            "data": base64.standard_b64encode(IMAGE.png).decode("ascii"),
        },
    }
    assert text_block == {"type": "text", "text": read_user_text(IMAGE)}


def test_answer_request_with_reading_and_excerpts():
    fake = FakeApi(ok(ANSWERS.model_dump_json()))
    backend, _ = make_backend(fake)

    assert backend.answer(IMAGE, READING, EXCERPTS) == ANSWERS

    [request] = fake.requests
    body = request.body
    assert_clean(body)
    assert request.headers["anthropic-beta"] == FALLBACK_BETA
    assert body["model"] == "claude-opus-5-5"
    assert body["system"] == ANSWER_SYSTEM
    assert body["output_config"] == {
        "effort": "high",
        "format": {"type": "json_schema", "schema": anthropic.transform_schema(Answers)},
    }
    image_block, text_block = body["messages"][0]["content"]
    assert image_block["type"] == "image"
    assert text_block["text"] == answer_user_text(READING, EXCERPTS, justifications=False)
    assert "La protéine Gs active l'adénylate cyclase." in text_block["text"]
    assert "<transcription>" in text_block["text"]


def test_answer_request_without_reading_nor_excerpts_and_with_justifications():
    fake = FakeApi(ok(ANSWERS.model_dump_json()))
    backend, _ = make_backend(fake, justifications=True)

    assert backend.answer(IMAGE, None, []) == ANSWERS

    text = fake.bodies[0]["messages"][0]["content"][1]["text"]
    assert text == answer_user_text(None, [], justifications=True)
    assert "Aucun extrait de cours" in text
    assert "Aucune transcription" in text
    assert text != answer_user_text(None, [], justifications=False)


def test_haiku_5_5_gets_no_server_side_fallback_and_no_fast_mode():
    fake = FakeApi(ok(READING.model_dump_json()))
    backend, _ = make_backend(fake, modele_lecture="claude-haiku-5-5", mode_rapide=True)

    backend.read(IMAGE)

    [request] = fake.requests
    assert request.path == "/v1/messages"
    assert "anthropic-beta" not in request.headers
    assert_clean(request.body)
    assert set(request.body) == {"model", "max_tokens", "system", "messages", "output_config", "thinking"}
    assert request.body["model"] == "claude-haiku-5-5"
    assert request.body["thinking"] == {"type": "adaptive"}
    assert request.body["output_config"]["effort"] == "low"


def test_unknown_model_gets_only_what_every_model_accepts(caplog):
    fake = FakeApi(ok(ANSWERS.model_dump_json()))
    backend, _ = make_backend(fake, modele_reponse="claude-opus-9", mode_rapide=True)

    with caplog.at_level(logging.WARNING, logger=LOGGER):
        backend.answer(IMAGE, None, [])
        backend.answer(IMAGE, None, [])

    for request in fake.requests:
        assert "anthropic-beta" not in request.headers
        assert_clean(request.body)
        assert set(request.body) == {"model", "max_tokens", "system", "messages", "output_config"}
        assert request.body["output_config"] == {
            "format": {"type": "json_schema", "schema": anthropic.transform_schema(Answers)}
        }
    warnings = [r for r in caplog.records if "inconnu de l'application" in r.getMessage()]
    assert len(warnings) == 1  # signalé une seule fois, pas à chaque capture


@pytest.mark.parametrize("fast", [True, False])
def test_fast_mode_is_sent_only_when_enabled(fast):
    fake = FakeApi(ok(ANSWERS.model_dump_json()))
    backend, _ = make_backend(fake, mode_rapide=fast)

    backend.answer(IMAGE, None, [])

    body = fake.bodies[0]
    assert_clean(body)
    assert body.get("speed") == ("fast" if fast else None)
    assert ("speed" in body) is fast
    assert body["fallbacks"] == "default"


def test_deep_efforts_get_a_larger_token_limit():
    fake = FakeApi(ok(ANSWERS.model_dump_json()))
    backend, _ = make_backend(fake, effort_reponse="max")

    backend.answer(IMAGE, None, [])

    assert fake.bodies[0]["max_tokens"] == MAX_TOKENS_DEEP
    assert fake.bodies[0]["output_config"]["effort"] == "max"


@pytest.mark.parametrize(
    ("model", "thinking", "fallback", "fast", "effort_for_xhigh"),
    [
        ("claude-opus-5-5", True, True, True, "xhigh"),
        ("claude-opus-5", True, True, False, "xhigh"),
        ("claude-sonnet-5-5", True, True, False, "xhigh"),
        ("claude-fable-5-2", True, True, True, "xhigh"),
        ("claude-fable-5-1", True, True, False, "xhigh"),
        ("claude-fable-5", True, True, False, "xhigh"),
        ("claude-haiku-5-5", True, False, False, "xhigh"),
        ("claude-sonnet-5", True, False, False, "xhigh"),
        ("claude-opus-4-8", True, False, False, "xhigh"),
        ("claude-opus-4-6", True, False, False, "high"),
        ("claude-sonnet-4-6", True, False, False, "high"),
        ("claude-opus-4-5", False, False, False, "high"),
        ("claude-haiku-4-5", False, False, False, None),
        ("claude-haiku-4-5-20251001", False, False, False, None),
        ("  Claude-Opus-5-5 ", True, True, True, "xhigh"),
        ("claude-opus-5-6", False, False, False, None),
        ("claude-mythos-5-2", False, False, False, None),
        ("gpt-4o", False, False, False, None),
        ("", False, False, False, None),
    ],
)
def test_request_rules_per_model(model, thinking, fallback, fast, effort_for_xhigh):
    params = build_request(
        model=model,
        effort="xhigh",
        system="s",
        content=[{"type": "text", "text": "t"}],
        output_type=Answers,
        fallback=True,
        fast=True,
    )
    assert_clean(params)
    assert (params.get("thinking") == {"type": "adaptive"}) is thinking
    assert ("thinking" in params) is thinking
    assert (params.get("fallbacks") == "default") is fallback
    assert (params.get("betas") == [FALLBACK_BETA]) is fallback
    assert (params.get("speed") == "fast") is fast
    assert params["output_config"].get("effort") == effort_for_xhigh
    assert params["output_config"]["format"]["type"] == "json_schema"


def test_fallback_and_fast_are_only_sent_when_asked():
    params = build_request(
        model="claude-opus-5-5",
        effort="high",
        system="s",
        content=[],
        output_type=Reading,
        fallback=False,
        fast=False,
    )
    assert not {"betas", "fallbacks", "speed"} & set(params)


def test_unknown_ids_share_the_default_rules():
    assert rules_for("claude-opus-9") is DEFAULT_RULES
    assert rules_for("claude-opus-5-5") is not DEFAULT_RULES
    assert effective_effort(DEFAULT_RULES, "high") is None
    assert effective_effort(rules_for("claude-opus-4-5"), "max") == "high"
    assert effective_effort(rules_for("claude-opus-5-5"), "inconnu") is None


@pytest.mark.parametrize("model_type", [Reading, Answers])
def test_schemas_fit_structured_outputs(model_type):
    """Tous les objets sont fermés et tous leurs champs obligatoires, comme l'exige l'API."""
    schema = anthropic.transform_schema(model_type)
    objects = [schema, *schema.get("$defs", {}).values()]
    assert len(objects) >= 3
    for node in objects:
        assert node["type"] == "object"
        assert node["additionalProperties"] is False
        assert sorted(node["required"]) == sorted(node["properties"])


def test_jpeg_capture_is_declared_as_jpeg():
    buffer = io.BytesIO()
    Image.new("RGB", (32, 20), "white").save(buffer, "JPEG")
    fake = FakeApi(ok(READING.model_dump_json()))
    backend, _ = make_backend(fake)

    backend.read(ScreenImage(png=buffer.getvalue(), width=32, height=20))

    assert fake.bodies[0]["messages"][0]["content"][0]["source"]["media_type"] == "image/jpeg"


def test_empty_capture_is_rejected_without_calling_the_api():
    fake = FakeApi(ok(READING.model_dump_json()))
    backend, _ = make_backend(fake)

    error = expect_error(lambda: backend.read(ScreenImage(png=b"", width=0, height=0)))

    assert "vide" in str(error)
    assert fake.requests == []


# --------------------------------------------------------------------------
# Lecture de la réponse
# --------------------------------------------------------------------------


def test_usage_is_logged_at_info(caplog):
    fake = FakeApi(ok(READING.model_dump_json()))
    backend, _ = make_backend(fake)

    with caplog.at_level(logging.INFO, logger=LOGGER):
        backend.read(IMAGE)

    [record] = [r for r in caplog.records if r.levelno == logging.INFO]
    line = record.getMessage()
    assert "lecture" in line
    assert "claude-opus-5-5" in line
    assert "6200 jetons en entrée" in line
    assert "1800 en sortie" in line
    assert "400 de réflexion" in line
    assert "req_factice" in line
    assert "en repli" not in line
    assert FAKE_KEY not in caplog.text


def test_refused_answer_raises_a_french_error():
    fake = FakeApi(refusal("bio"))
    backend, _ = make_backend(fake)

    error = expect_error(lambda: backend.answer(IMAGE, READING, EXCERPTS))

    assert "refusé" in str(error)
    assert "bio" in error.detail
    assert len(fake.requests) == 1


def test_refusal_with_partial_output_is_still_a_refusal():
    partial = [{"type": "text", "text": '{"questions": [{"numero": "1", "verd'}]
    fake = FakeApi(ok(None, stop_reason="refusal", content=partial, stop_details=None))
    backend, _ = make_backend(fake)

    error = expect_error(lambda: backend.answer(IMAGE, None, []))

    assert "refusé" in str(error)


def test_refused_read_is_retried_once_with_the_answer_model(caplog):
    fake = FakeApi(by_model(claude_haiku_5_5=refusal("bio"), claude_opus_5_5=ok(READING.model_dump_json())))
    backend, _ = make_backend(fake, modele_lecture="claude-haiku-5-5")

    with caplog.at_level(logging.WARNING, logger=LOGGER):
        assert backend.read(IMAGE) == READING

    first, second = fake.requests
    assert first.body["model"] == "claude-haiku-5-5"
    assert "fallbacks" not in first.body
    assert second.body["model"] == "claude-opus-5-5"
    assert second.body["fallbacks"] == "default"
    assert second.headers["anthropic-beta"] == FALLBACK_BETA
    # C'est toujours une lecture : même consigne, même effort, même contenu.
    assert second.body["system"] == READ_SYSTEM
    assert second.body["output_config"]["effort"] == "low"
    assert second.body["messages"] == first.body["messages"]
    assert "nouvel essai avec claude-opus-5-5" in caplog.text


def test_refused_read_is_not_retried_when_both_models_are_the_same():
    fake = FakeApi(refusal("bio"))
    backend, _ = make_backend(fake)

    error = expect_error(lambda: backend.read(IMAGE))

    assert "refusé" in str(error)
    assert len(fake.requests) == 1


def test_read_refused_by_both_models_fails_after_two_requests():
    fake = FakeApi(refusal(None))
    backend, _ = make_backend(fake, modele_lecture="claude-haiku-5-5")

    error = expect_error(lambda: backend.read(IMAGE))

    assert "refusé" in str(error)
    assert [b["model"] for b in fake.bodies] == ["claude-haiku-5-5", "claude-opus-5-5"]


def test_refused_read_is_not_retried_when_time_is_nearly_up():
    clock_holder: list[FakeClock] = []

    def slow_refusal(request: httpx2.Request) -> httpx2.Response:
        clock_holder[0].now += 140  # il reste 10 s sur 150
        return refusal("bio")(request)

    fake = FakeApi(slow_refusal)
    backend, clock = make_backend(fake, modele_lecture="claude-haiku-5-5")
    clock_holder.append(clock)

    expect_error(lambda: backend.read(IMAGE))

    assert len(fake.requests) == 1


def test_truncated_answer_reports_max_tokens():
    fake = FakeApi(ok('{"questions": [{"numero": "1", "verdicts": [', stop_reason="max_tokens"))
    backend, _ = make_backend(fake)

    error = expect_error(lambda: backend.answer(IMAGE, None, []))

    assert "trop longue" in str(error)
    assert "effort_reponse" in str(error)
    assert "max_tokens" in error.detail
    assert len(fake.requests) == 1


def test_truncated_read_names_the_read_setting():
    fake = FakeApi(ok(None, stop_reason="max_tokens"))
    backend, _ = make_backend(fake)

    error = expect_error(lambda: backend.read(IMAGE))

    assert "effort_lecture" in str(error)


@pytest.mark.parametrize(
    "text",
    [
        "Je ne vois aucune question sur cette capture.",
        '{"questions": [{"numero": 1}]}',
        '{"questions": [',
        "",
    ],
)
def test_malformed_output_raises_a_french_error(text):
    fake = FakeApi(ok(text))
    backend, _ = make_backend(fake)

    error = expect_error(lambda: backend.answer(IMAGE, None, []))

    assert "illisible" in str(error)
    assert "req_factice" in error.detail
    assert len(fake.requests) == 1


def test_response_without_any_text_block_is_malformed():
    fake = FakeApi(ok(None))
    backend, _ = make_backend(fake)

    error = expect_error(lambda: backend.read(IMAGE))

    assert "illisible" in str(error)


def test_json_wrapped_in_a_code_fence_is_accepted():
    fake = FakeApi(ok(f"```json\n{ANSWERS.model_dump_json()}\n```"))
    backend, _ = make_backend(fake)

    assert backend.answer(IMAGE, None, []) == ANSWERS


def test_empty_question_list_is_a_valid_reading():
    fake = FakeApi(ok('{"questions": []}'))
    backend, _ = make_backend(fake)

    assert backend.read(IMAGE) == Reading(questions=[])


def test_answer_served_by_the_fallback_model(caplog):
    content = [
        {"type": "text", "text": '{"questions": [{"numero"'},
        {
            "type": "fallback",
            "from": {"model": "claude-opus-5-5"},
            "to": {"model": "claude-opus-5"},
            "trigger": {"type": "refusal", "category": "bio"},
        },
        {"type": "thinking", "thinking": "", "signature": "c2ln"},
        {"type": "text", "text": ANSWERS.model_dump_json()},
    ]
    usage = {
        "input_tokens": 7000,
        "output_tokens": 900,
        "iterations": [
            {
                "type": "message",
                "model": "claude-opus-5-5",
                "input_tokens": 7000,
                "output_tokens": 0,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
            },
            {
                "type": "fallback_message",
                "model": "claude-opus-5",
                "input_tokens": 7000,
                "output_tokens": 900,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
            },
        ],
    }

    def reply(request: httpx2.Request) -> httpx2.Response:
        body = message_body(model="claude-opus-5", content=content, usage=usage)
        return httpx2.Response(200, json=body, headers={"request-id": "req_repli"})

    backend, _ = make_backend(FakeApi(reply))

    with caplog.at_level(logging.INFO, logger=LOGGER):
        assert backend.answer(IMAGE, None, []) == ANSWERS

    assert "modèle claude-opus-5 (en repli de claude-opus-5-5)" in caplog.text
    assert "claude-opus-5-5 7000+0, claude-opus-5 7000+900" in caplog.text


def test_dated_model_id_in_the_response_is_not_reported_as_a_fallback(caplog):
    fake = FakeApi(ok(ANSWERS.model_dump_json(), model="claude-haiku-4-5-20251001"))
    backend, _ = make_backend(fake, modele_reponse="claude-haiku-4-5")

    with caplog.at_level(logging.INFO, logger=LOGGER):
        backend.answer(IMAGE, None, [])

    assert "claude-haiku-4-5-20251001" in caplog.text
    assert "en repli" not in caplog.text


# --------------------------------------------------------------------------
# Erreurs de l'API
# --------------------------------------------------------------------------


def test_rejected_key_points_to_the_key_file():
    fake = FakeApi(api_error(401, "authentication_error", f"invalid x-api-key: {FAKE_KEY}"))
    backend, _ = make_backend(fake)

    error = expect_error(lambda: backend.answer(IMAGE, None, []))

    assert "Clé API refusée" in str(error)
    assert "cle_api.txt" in str(error)
    assert "[clé masquée]" in error.detail  # la clé renvoyée par le serveur n'atteint pas le journal
    assert "AuthenticationError" in error.detail
    assert "req_erreur" in error.detail
    assert len(fake.requests) == 1


@pytest.fixture
def no_credentials(monkeypatch, tmp_path):
    """Aucune clé nulle part : ni variable d'environnement, ni profil du SDK sur le disque."""
    for name in list(os.environ):
        if name.startswith("ANTHROPIC_"):
            monkeypatch.delenv(name)
    for name in ("HOME", "USERPROFILE", "APPDATA", "XDG_CONFIG_HOME"):
        monkeypatch.setenv(name, str(tmp_path))


def test_missing_key_is_reported_without_any_request(no_credentials):
    fake = FakeApi(ok(READING.model_dump_json()))
    client = Anthropic(http_client=DefaultHttpxClient(transport=httpx2.MockTransport(fake)))
    backend = ApiBackend(AIConfig(), client=client)

    for call in (lambda: backend.read(IMAGE), lambda: backend.answer(IMAGE, None, []), backend.check):
        error = expect_error(call)
        assert "Clé API absente" in str(error)
        assert "cle_api.txt" in str(error)
    assert fake.requests == []


def test_default_client_picks_up_a_key_added_later(no_credentials, monkeypatch):
    fake = FakeApi(ok(READING.model_dump_json()))
    created: list[dict[str, Any]] = []
    real_client = anthropic.Anthropic

    def offline_client(**kwargs: Any) -> Anthropic:
        created.append(kwargs)
        return real_client(http_client=DefaultHttpxClient(transport=httpx2.MockTransport(fake)), **kwargs)

    monkeypatch.setattr(api_backend.anthropic, "Anthropic", offline_client)
    backend = ApiBackend(AIConfig())

    error = expect_error(lambda: backend.read(IMAGE))
    assert "Clé API absente" in str(error)
    assert fake.requests == []

    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)  # ce que fait load_api_key
    assert backend.read(IMAGE) == READING
    assert backend.read(IMAGE) == READING

    assert fake.requests[0].headers["authorization"] == f"Bearer {FAKE_KEY}"
    assert len(created) == 2  # recréé une fois la clé présente, puis réutilisé
    assert all(kwargs == {"max_retries": 0} for kwargs in created)


@pytest.mark.parametrize(
    "reply",
    [
        api_error(402, "billing_error", "Your credit balance is too low."),
        api_error(
            400,
            "invalid_request_error",
            "Your credit balance is too low to access the Anthropic API. "
            "Please go to Plans & Billing to upgrade or purchase credits.",
        ),
    ],
)
def test_no_credit(reply):
    fake = FakeApi(reply)
    backend, _ = make_backend(fake)

    error = expect_error(lambda: backend.answer(IMAGE, None, []))

    assert "Crédit API épuisé" in str(error)
    assert len(fake.requests) == 1  # inutile de réessayer sans les options facultatives


def test_spend_limit_is_not_retried():
    details = {"error_code": "enforced_spend_limit_reached"}
    fake = FakeApi(api_error(429, "rate_limit_error", "Spend limit reached.", details=details))
    backend, clock = make_backend(fake)

    error = expect_error(lambda: backend.answer(IMAGE, None, []))

    assert "Plafond de dépense" in str(error)
    assert len(fake.requests) == 1
    assert clock.sleeps == []


@pytest.mark.parametrize(
    ("phase", "setting"),
    [("read", "modele_lecture"), ("answer", "modele_reponse")],
)
def test_unknown_model_names_the_setting_to_fix(phase, setting):
    fake = FakeApi(api_error(404, "not_found_error", "model: claude-opus-55"))
    backend, _ = make_backend(fake, **{setting: "claude-opus-55"})

    call = (lambda: backend.read(IMAGE)) if phase == "read" else (lambda: backend.answer(IMAGE, None, []))
    error = expect_error(call)

    assert "claude-opus-55" in str(error)
    assert setting in str(error)
    assert "config.toml" in str(error)
    assert len(fake.requests) == 1


def test_permission_denied_and_too_large():
    fake = FakeApi(api_error(403, "permission_error", "Not allowed."), api_error(413, "request_too_large", "Too big."))
    backend, _ = make_backend(fake)

    assert "Accès refusé" in str(expect_error(lambda: backend.answer(IMAGE, None, [])))
    assert "trop lourde" in str(expect_error(lambda: backend.answer(IMAGE, None, [])))


def test_rate_limit_waits_as_asked_then_succeeds():
    limited = api_error(429, "rate_limit_error", "Slow down.", headers={"retry-after": "3"})
    fake = FakeApi(limited, ok(ANSWERS.model_dump_json()))
    backend, clock = make_backend(fake)

    assert backend.answer(IMAGE, None, []) == ANSWERS

    assert clock.sleeps == [3.0]
    assert len(fake.requests) == 2
    assert fake.requests[1].timeout["read"] == 147.0  # le délai restant, pas un délai complet


def test_persistent_rate_limit_gives_up_after_two_retries():
    fake = FakeApi(api_error(429, "rate_limit_error", "Slow down."))
    backend, clock = make_backend(fake)

    error = expect_error(lambda: backend.answer(IMAGE, None, []))

    assert "Trop de demandes" in str(error)
    assert "une minute" in str(error)
    assert len(fake.requests) == 3  # les nouveaux essais du SDK ne s'y ajoutent pas
    assert clock.sleeps == [5.0, 5.0]


def test_rate_limit_longer_than_the_budget_fails_at_once():
    fake = FakeApi(api_error(429, "rate_limit_error", "Slow down.", headers={"retry-after": "600"}))
    backend, clock = make_backend(fake)

    error = expect_error(lambda: backend.answer(IMAGE, None, []))

    assert "600 s" in str(error)
    assert len(fake.requests) == 1
    assert clock.sleeps == []


def test_overload_is_retried_then_succeeds():
    fake = FakeApi(api_error(529, "overloaded_error", "Overloaded"), ok(READING.model_dump_json()))
    backend, clock = make_backend(fake)

    assert backend.read(IMAGE) == READING

    assert clock.sleeps == [2.0]
    assert len(fake.requests) == 2


@pytest.mark.parametrize(
    "reply",
    [
        api_error(529, "overloaded_error", "Overloaded"),
        api_error(500, "api_error", "Internal server error"),
        lambda request: httpx2.Response(503, text="<html>Service Unavailable</html>"),
    ],
)
def test_persistent_server_error(reply):
    fake = FakeApi(reply)
    backend, clock = make_backend(fake)

    error = expect_error(lambda: backend.answer(IMAGE, None, []))

    assert "serveurs d'Anthropic" in str(error)
    assert len(fake.requests) == 3
    assert clock.sleeps == [2.0, 4.0]


def test_retries_stop_when_the_time_budget_runs_out():
    holder: list[FakeClock] = []

    def slow_overload(request: httpx2.Request) -> httpx2.Response:
        holder[0].now += 15
        return api_error(529, "overloaded_error", "Overloaded")(request)

    fake = FakeApi(slow_overload)
    backend, clock = make_backend(fake, delai_max_s=40)
    holder.append(clock)

    error = expect_error(lambda: backend.answer(IMAGE, None, []))

    assert "serveurs d'Anthropic" in str(error)
    # 1er essai : 15 s. Attente : 2 s. 2e essai avec les 23 s restantes, puis plus assez de temps.
    assert [r.timeout["read"] for r in fake.requests] == [40.0, 23.0]
    assert clock.sleeps == [2.0]
    assert clock.now - 1_000.0 <= 40


def test_timeout_respects_the_configured_delay():
    holder: list[FakeClock] = []

    def never_answers(request: httpx2.Request) -> httpx2.Response:
        holder[0].now += request.extensions["timeout"]["read"]
        raise httpx2.ReadTimeout("The read operation timed out", request=request)

    fake = FakeApi(never_answers)
    backend, clock = make_backend(fake, delai_max_s=90)
    holder.append(clock)

    error = expect_error(lambda: backend.answer(IMAGE, None, []))

    assert "Pas de réponse en 90 s" in str(error)
    assert "delai_max_s" in str(error)
    assert "APITimeoutError" in error.detail
    [request] = fake.requests  # un seul essai : le délai est consommé
    assert request.timeout == {"connect": 15.0, "read": 90.0, "write": 90.0, "pool": 90.0}
    assert clock.now - 1_000.0 == 90
    assert clock.sleeps == []


def test_very_short_delay_still_leaves_a_minimum():
    fake = FakeApi(ok(READING.model_dump_json()))
    backend, _ = make_backend(fake, delai_max_s=0)

    backend.read(IMAGE)

    assert fake.requests[0].timeout == {"connect": 5.0, "read": 5.0, "write": 5.0, "pool": 5.0}


def test_connection_error_is_retried_then_reported():
    def offline(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("[Errno 11001] getaddrinfo failed", request=request)

    fake = FakeApi(offline)
    backend, clock = make_backend(fake)

    error = expect_error(lambda: backend.read(IMAGE))

    assert "Connexion à Anthropic impossible" in str(error)
    assert "getaddrinfo failed" in error.detail
    assert len(fake.requests) == 3
    assert clock.sleeps == [2.0, 4.0]


def test_connect_timeout_is_reported_as_a_connection_problem():
    holder: list[FakeClock] = []

    def unreachable(request: httpx2.Request) -> httpx2.Response:
        holder[0].now += request.extensions["timeout"]["connect"]
        raise httpx2.ConnectTimeout("timed out", request=request)

    fake = FakeApi(unreachable)
    backend, clock = make_backend(fake)
    holder.append(clock)

    error = expect_error(lambda: backend.answer(IMAGE, None, []))

    assert "Connexion à Anthropic impossible" in str(error)
    assert [r.timeout["read"] for r in fake.requests] == [150.0, 133.0, 114.0]


def test_dropped_connection_then_success():
    def dropped(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.RemoteProtocolError("Server disconnected without sending a response.", request=request)

    fake = FakeApi(dropped, ok(ANSWERS.model_dump_json()))
    backend, clock = make_backend(fake)

    assert backend.answer(IMAGE, None, []) == ANSWERS
    assert clock.sleeps == [2.0]


def test_html_page_instead_of_the_api_answer():
    fake = FakeApi(lambda request: httpx2.Response(200, text="<html>Portail Wi-Fi</html>", headers={"content-type": "text/html"}))
    backend, _ = make_backend(fake)

    error = expect_error(lambda: backend.read(IMAGE))

    assert "Réponse inattendue du serveur" in str(error)


def test_unparseable_body_is_wrapped_in_a_backend_error():
    fake = FakeApi(lambda request: httpx2.Response(200, text="pas du JSON", headers={"content-type": "application/json"}))
    backend, _ = make_backend(fake)

    error = expect_error(lambda: backend.read(IMAGE))

    assert "Erreur inattendue" in str(error)
    assert len(fake.requests) == 1


# --------------------------------------------------------------------------
# Options facultatives rejetées par l'API
# --------------------------------------------------------------------------

BETA_REJECTED = api_error(
    400,
    "invalid_request_error",
    f"Unexpected value(s) `{FALLBACK_BETA}` for the `anthropic-beta` header.",
)


def test_rejected_fallback_option_is_dropped_and_remembered(caplog):
    fake = FakeApi(BETA_REJECTED, ok(READING.model_dump_json()))
    backend, clock = make_backend(fake)

    with caplog.at_level(logging.WARNING, logger=LOGGER):
        assert backend.read(IMAGE) == READING
    assert backend.read(IMAGE) == READING

    first, second, third = fake.requests
    assert first.body["fallbacks"] == "default"
    for request in (second, third):  # le 3e part directement sans l'option
        assert "fallbacks" not in request.body
        assert "anthropic-beta" not in request.headers
        assert request.body["thinking"] == {"type": "adaptive"}
        assert request.body["output_config"]["effort"] == "low"
    assert clock.sleeps == []
    assert "nouvel essai sans" in caplog.text


def test_rejected_fast_mode_is_dropped_but_fallback_is_kept():
    fake = FakeApi(api_error(400, "invalid_request_error", "speed: fast mode is not available."), ok(ANSWERS.model_dump_json()))
    backend, _ = make_backend(fake, mode_rapide=True)

    assert backend.answer(IMAGE, None, []) == ANSWERS
    assert backend.answer(IMAGE, None, []) == ANSWERS

    first, second, third = fake.bodies
    assert first["speed"] == "fast"
    for body in (second, third):
        assert "speed" not in body
        assert body["fallbacks"] == "default"


def test_bad_request_that_no_option_explains_is_reported_and_not_remembered():
    fake = FakeApi(api_error(400, "invalid_request_error", "messages.0.content.0.image: image exceeds 5 MB"))
    backend, _ = make_backend(fake, mode_rapide=True)

    error = expect_error(lambda: backend.answer(IMAGE, None, []))

    assert "Requête refusée par l'API" in str(error)
    assert "image exceeds 5 MB" in error.detail
    assert [(b.get("speed"), b.get("fallbacks")) for b in fake.bodies] == [
        ("fast", "default"),
        (None, "default"),
        (None, None),
    ]
    # La requête suivante repart avec toutes les options.
    fake.replies = [ok(ANSWERS.model_dump_json())]
    backend.answer(IMAGE, None, [])
    assert fake.bodies[-1]["speed"] == "fast"
    assert fake.bodies[-1]["fallbacks"] == "default"


def test_fast_mode_rate_limit_falls_back_to_standard_speed():
    fake = FakeApi(api_error(429, "rate_limit_error", "Fast mode rate limit."), ok(ANSWERS.model_dump_json()))
    backend, clock = make_backend(fake, mode_rapide=True)

    assert backend.answer(IMAGE, None, []) == ANSWERS
    assert backend.answer(IMAGE, None, []) == ANSWERS

    first, second, third = fake.bodies
    assert first["speed"] == "fast"
    assert "speed" not in second
    assert clock.sleeps == []
    assert third["speed"] == "fast"  # limite passagère : non mémorisée


# --------------------------------------------------------------------------
# check()
# --------------------------------------------------------------------------


def model_info(model_id: str, **overrides: Any) -> Reply:
    payload = {
        "type": "model",
        "id": model_id,
        "display_name": model_id,
        "created_at": "2026-09-01T00:00:00Z",
        "lifecycle": "active",
        "max_input_tokens": 1_000_000,
        "max_tokens": 128_000,
        "capabilities": {"image_input": {"supported": True}, "structured_outputs": {"supported": True}},
        **overrides,
    }
    return lambda request: httpx2.Response(200, json=payload, headers={"request-id": "req_modele"})


def any_model() -> Reply:
    return lambda request: model_info(request.url.path.rsplit("/", 1)[-1])(request)


def test_check_with_a_single_model():
    fake = FakeApi(any_model())
    backend, _ = make_backend(fake)

    assert backend.check() == "API Anthropic : clé valide, modèle claude-opus-5-5 disponible."

    [request] = fake.requests
    assert (request.method, request.path) == ("GET", "/v1/models/claude-opus-5-5")
    assert request.body is None  # vérification gratuite : aucun appel au modèle
    assert request.timeout["read"] == 10.0


def test_check_with_two_models():
    fake = FakeApi(any_model())
    backend, _ = make_backend(fake, modele_lecture="claude-haiku-5-5")

    status = backend.check()

    assert status == (
        "API Anthropic : clé valide, modèles claude-opus-5-5 (correction) "
        "et claude-haiku-5-5 (lecture) disponibles."
    )
    assert [r.path for r in fake.requests] == ["/v1/models/claude-opus-5-5", "/v1/models/claude-haiku-5-5"]


def test_check_reports_a_rejected_key():
    fake = FakeApi(api_error(401, "authentication_error", "invalid x-api-key"))
    backend, _ = make_backend(fake)

    error = expect_error(backend.check)

    assert "Clé API refusée" in str(error)
    assert "cle_api.txt" in str(error)


def test_check_names_the_setting_of_a_missing_model():
    fake = FakeApi(any_model(), api_error(404, "not_found_error", "model: claude-haiku-55"))
    backend, _ = make_backend(fake, modele_lecture="claude-haiku-55")

    error = expect_error(backend.check)

    assert "claude-haiku-55" in str(error)
    assert "modele_lecture" in str(error)


def test_check_rejects_an_empty_model_setting():
    fake = FakeApi(any_model())
    backend, _ = make_backend(fake, modele_reponse="  ")

    error = expect_error(backend.check)

    assert "modele_reponse est vide" in str(error)
    assert fake.requests == []


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"capabilities": {"image_input": {"supported": False}}}, "ne lit pas les images"),
        ({"capabilities": {"structured_outputs": {"supported": False}}}, "format attendu"),
        ({"lifecycle": "retired"}, "retiré"),
    ],
)
def test_check_rejects_a_model_that_cannot_do_the_job(overrides, expected):
    fake = FakeApi(model_info("claude-opus-5-5", **overrides))
    backend, _ = make_backend(fake)

    error = expect_error(backend.check)

    assert expected in str(error)
    assert "modele_reponse" in str(error)


def test_check_remarks():
    fake = FakeApi(model_info("claude-opus-9", lifecycle="deprecated", capabilities=None))
    backend, _ = make_backend(fake, modele_reponse="claude-opus-9", modele_lecture="claude-opus-9", mode_rapide=True)

    status = backend.check()

    assert status.startswith("API Anthropic : clé valide, modèle claude-opus-9 disponible (")
    assert "sera bientôt retiré" in status
    assert "n'est pas connu de l'application" in status
    assert "mode rapide indisponible" in status


def test_check_without_network(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda seconds: None)  # attente du SDK entre ses deux essais

    def offline(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("[Errno 11001] getaddrinfo failed", request=request)

    fake = FakeApi(offline)
    backend, _ = make_backend(fake)

    error = expect_error(backend.check)

    assert "Connexion à Anthropic impossible" in str(error)
    assert len(fake.requests) == 2


def test_check_when_the_server_does_not_answer(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda seconds: None)

    def never_answers(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ReadTimeout("The read operation timed out", request=request)

    backend, _ = make_backend(FakeApi(never_answers))

    error = expect_error(backend.check)

    assert "Anthropic ne répond pas" in str(error)
    assert "delai_max_s" not in str(error)


# --------------------------------------------------------------------------
# Avec le pipeline complet
# --------------------------------------------------------------------------


class FakeSearcher:
    def __init__(self, excerpts: list[Excerpt]) -> None:
        self.excerpts = excerpts

    def search(self, query: str, limit: int = 8) -> list[Excerpt]:
        return self.excerpts[:limit]

    def is_empty(self) -> bool:
        return False


def test_pipeline_without_courses_reads_then_answers():
    fake = FakeApi(by_system(read=ok(READING.model_dump_json()), answer=ok(ANSWERS.model_dump_json())))
    backend = ApiBackend(AIConfig(), client=make_client(fake))

    result = correct_screen(IMAGE, backend)

    assert result.backend == "api"
    assert result.resume() == "1: ACD"
    assert result.questions[0].cochees == "B"
    assert result.questions[0].positions["A"] == (120, 200)
    assert [b["system"] for b in fake.bodies] == [READ_SYSTEM, ANSWER_SYSTEM]
    for body in fake.bodies:
        assert_clean(body)


def test_pipeline_with_courses_feeds_the_reading_and_excerpts_to_the_answer():
    fake = FakeApi(by_system(read=ok(READING.model_dump_json()), answer=ok(ANSWERS.model_dump_json())))
    backend = ApiBackend(AIConfig(modele_lecture="claude-haiku-5-5"), client=make_client(fake))

    result = correct_screen(IMAGE, backend, FakeSearcher(EXCERPTS))

    assert result.resume() == "1: ACD"
    read_body, answer_body = fake.bodies
    assert (read_body["model"], answer_body["model"]) == ("claude-haiku-5-5", "claude-opus-5-5")
    text = answer_body["messages"][0]["content"][1]["text"]
    assert text == answer_user_text(READING, EXCERPTS, justifications=False)


def test_pipeline_survives_a_refused_read():
    fake = FakeApi(by_system(read=refusal("bio"), answer=ok(ANSWERS.model_dump_json())))
    backend = ApiBackend(AIConfig(), client=make_client(fake))

    result = correct_screen(IMAGE, backend, FakeSearcher(EXCERPTS))

    assert result.resume() == "1: ACD"
    assert result.questions[0].cochees is None
