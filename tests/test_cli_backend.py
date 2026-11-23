"""Moteur Claude Code : tests avec un faux programme « claude », plus un essai réel facultatif."""

from __future__ import annotations

import base64
import json
import os
import stat
import sys
import textwrap
from pathlib import Path

import pytest

from correcteur.ai.base import Backend, BackendError
from correcteur.ai.cli_backend import CliBackend, _extract_json, compact_schema, find_executable
from correcteur.config import AIConfig
from correcteur.models import Answers, Excerpt, Reading, ScreenImage

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="le faux programme est un script POSIX")

IMAGE = ScreenImage(png=b"\x89PNG\r\n\x1a\nfaux", width=800, height=600)
READING = {
    "questions": [
        {
            "numero": "1",
            "debut_visible": True,
            "type": "QRM",
            "enonce": "Énoncé",
            "propositions": [{"lettre": "A", "texte": "a", "cochee": False, "x": 10, "y": 20}],
            "complete": True,
            "requetes": ["mot clé"],
        }
    ]
}
ANSWERS = {
    "questions": [
        {
            "numero": "1",
            "cocher": "vraies",
            "verdicts": [{"lettre": "A", "vraie": True}],
            "reponse_libre": "",
            "source": "cours",
            "confiance": "haute",
            "note": "",
        }
    ]
}

FAKE = textwrap.dedent(
    """\
    #!{python}
    import json, os, sys, time
    record = os.environ["FAKE_RECORD"]
    args = sys.argv[1:]
    if args == ["--version"]:
        print("9.9.9 (Claude Code)")
        sys.exit(0)
    if args[:2] == ["auth", "status"]:
        state = os.environ.get("FAKE_AUTH", "in")
        if state == "unsupported":
            sys.stderr.write("error: unknown command 'auth'")
            sys.exit(1)
        if "ANTHROPIC_API_KEY" in os.environ:
            state = "key-leaked"
        print(json.dumps({{"loggedIn": state == "in", "authMethod": "claude.ai"}}, indent=2))
        sys.exit(0)
    system_file = args[args.index("--system-prompt-file") + 1]
    json.dump({{"args": args, "stdin": sys.stdin.read(), "cwd": os.getcwd(),
               "system": open(system_file, encoding="utf-8").read(),
               "has_key": "ANTHROPIC_API_KEY" in os.environ}}, open(record, "w"))
    mode = os.environ.get("FAKE_MODE", "ok")
    if mode == "sleep":
        time.sleep(60)
    if mode == "crash":
        sys.stderr.write("boom")
        sys.exit(3)
    print(json.dumps({{"type": "system", "subtype": "init"}}))
    print("ligne parasite")
    print(os.environ["FAKE_RESULT"])
    """
)


@pytest.fixture()
def fake(tmp_path, monkeypatch):
    script = tmp_path / "claude"
    script.write_text(FAKE.format(python=sys.executable), encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    record = tmp_path / "record.json"
    monkeypatch.setenv("FAKE_RECORD", str(record))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-secret")

    def configure(result: dict | None = None, mode: str = "ok", **cfg):
        monkeypatch.setenv("FAKE_MODE", mode)
        monkeypatch.setenv("FAKE_RESULT", json.dumps(result or {}))
        backend = CliBackend(AIConfig(cli_commande=str(script), **cfg))
        return backend, lambda: json.loads(record.read_text())

    return configure


def ok(structured=None, text=""):
    return {"type": "result", "subtype": "success", "is_error": False, "result": text, "structured_output": structured}


def test_respecte_le_contrat():
    assert isinstance(CliBackend(AIConfig()), Backend)


def test_lecture_commande_et_entree(fake):
    backend, record = fake(ok(READING), cli_modele="opus", cli_modele_lecture="haiku")
    reading = backend.read(IMAGE)
    assert reading.questions[0].propositions[0].x == 10
    sent = record()
    args = sent["args"]
    assert args[0] == "-p" and args[args.index("--tools") + 1] == ""
    for flag in ("--strict-mcp-config", "--no-session-persistence", "--verbose"):
        assert flag in args
    assert args[args.index("--input-format") + 1] == "stream-json"
    assert args[args.index("--model") + 1] == "haiku"
    assert "Relève chaque question visible" in sent["system"]
    schema = args[args.index("--json-schema") + 1]
    assert schema.isascii() and "description" not in schema and "\n" not in schema
    assert json.loads(schema)["required"] == ["questions"]
    message = json.loads(sent["stdin"])["message"]
    assert base64.b64decode(message["content"][0]["source"]["data"]) == IMAGE.png
    assert "800 × 600" in message["content"][1]["text"]
    assert sent["has_key"] is False  # la clé API n'est pas transmise : on veut l'abonnement
    assert Path(sent["cwd"]).name.startswith("correcteur-claude-")


def test_correction_avec_extraits_et_sans_modele(fake):
    backend, record = fake(ok(ANSWERS), justifications=True)
    reading = Reading.model_validate(READING)
    answers = backend.answer(IMAGE, reading, [Excerpt("cardio.pdf", 4, "Le cours dit que...", 1.0, 3)])
    assert answers.questions[0].verdicts[0].vraie is True
    sent = record()
    assert "--model" not in sent["args"]
    text = json.loads(sent["stdin"])["message"]["content"][1]["text"]
    assert 'source="cardio.pdf" page="4"' in text and "Question 1 (QRM, complète)" in text
    assert "25 mots" in text
    assert "réponse attendue par ses enseignants" in sent["system"]


def test_repli_sur_le_texte_si_pas_de_sortie_structuree(fake):
    backend, _ = fake(ok(None, text="Voici :\n```json\n" + json.dumps(ANSWERS) + "\n```"))
    assert backend.answer(IMAGE, None, []).questions[0].numero == "1"


def test_sortie_invalide(fake):
    backend, _ = fake(ok({"questions": [{"numero": 1}]}))
    with pytest.raises(BackendError, match="illisible"):
        backend.answer(IMAGE, None, [])


@pytest.mark.parametrize(
    "result, expected",
    [
        ({"type": "result", "subtype": "success", "is_error": True, "result": "Invalid API key · Please run /login"}, "pas connecté à ton compte"),
        ({"type": "result", "subtype": "success", "is_error": True, "result": "You've hit your limit · resets 5:40am (UTC)"}, "resets 5:40am"),
        ({"type": "result", "subtype": "error_during_execution", "is_error": True, "result": "étrange"}, "a renvoyé une erreur"),
    ],
)
def test_erreurs_renvoyees_par_claude(fake, result, expected):
    backend, _ = fake(result)
    with pytest.raises(BackendError, match=expected) as info:
        backend.read(IMAGE)
    assert "sk-ant-secret" not in str(info.value) + info.value.detail


def test_plantage_sans_resultat(fake):
    backend, _ = fake(mode="crash")
    with pytest.raises(BackendError) as info:
        backend.read(IMAGE)
    assert "boom" in info.value.detail and "code 3" in info.value.detail


def test_delai_depasse(fake, monkeypatch):
    backend, _ = fake(mode="sleep")
    monkeypatch.setattr(backend.cfg, "delai_max_s", 10)
    monkeypatch.setattr("correcteur.ai.cli_backend.max", lambda *_: 1, raising=False)
    with pytest.raises(BackendError, match="pas répondu"):
        backend.read(IMAGE)


def test_programme_introuvable(tmp_path):
    backend = CliBackend(AIConfig(cli_commande=str(tmp_path / "absent")))
    with pytest.raises(BackendError, match="pas installé"):
        backend.read(IMAGE)
    with pytest.raises(BackendError, match="connecter_claude.bat"):
        backend.check()
    assert find_executable("programme-qui-n-existe-pas-xyz") is None


def test_verification(fake, monkeypatch):
    backend, _ = fake()
    # La clé API n'est pas transmise : l'état affiché est celui de l'abonnement.
    assert backend.check() == "abonnement Claude connecté (Claude Code 9.9.9)"
    monkeypatch.setenv("FAKE_AUTH", "out")
    with pytest.raises(BackendError, match="connecter_claude.bat"):
        backend.check()
    monkeypatch.setenv("FAKE_AUTH", "unsupported")
    assert "non vérifiée" in backend.check()


def test_outils():
    assert _extract_json('bla {"a": {"b": 1}} fin') == {"a": {"b": 1}}
    with pytest.raises(ValueError):
        _extract_json("rien")
    for model in (Reading, Answers):
        schema = compact_schema(model)
        assert schema.isascii() and not any(ch in schema for ch in "&|<>^%!\n")


@pytest.mark.skipif(os.environ.get("CORRECTEUR_TEST_CLI") != "1", reason="essai réel : CORRECTEUR_TEST_CLI=1")
def test_reel_lecture_et_correction():
    image_path = os.environ["CORRECTEUR_TEST_IMAGE"]
    data = Path(image_path).read_bytes()
    from PIL import Image

    with Image.open(image_path) as im:
        image = ScreenImage(data, *im.size)
    backend = CliBackend(AIConfig())
    print("\n" + backend.check())
    reading = backend.read(image)
    answers = backend.answer(image, reading, [])
    print({q.numero: "".join(v.lettre for v in q.verdicts if v.vraie) for q in answers.questions})
    assert reading.questions and answers.questions
