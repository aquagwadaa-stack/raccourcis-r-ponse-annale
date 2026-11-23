"""Ligne de commande."""

from __future__ import annotations

import pytest
from PySide6.QtGui import QColor, QImage

from correcteur.__main__ import main
from correcteur.ai import select
from correcteur.ai.base import BackendError
from correcteur.config import AIConfig, Config

from .test_app import FakeBackend


def run(capsys, *args):
    code = main(list(args))
    return code, capsys.readouterr().out


def test_preparer_cree_la_config_et_le_dossier(tmp_path, capsys):
    code, out = run(capsys, "--dossier", str(tmp_path), "preparer")
    assert code == 0 and (tmp_path / "config.toml").exists() and (tmp_path / "cours").is_dir()
    assert "config.toml créé" in out
    code, out = run(capsys, "--dossier", str(tmp_path), "preparer")
    assert "Rien à préparer" in out


def test_indexer_et_chercher(tmp_path, capsys):
    run(capsys, "--dossier", str(tmp_path), "preparer")
    code, out = run(capsys, "--dossier", str(tmp_path), "chercher", "insuline")
    assert code == 1 and "vide" in out
    code, out = run(capsys, "--dossier", str(tmp_path), "indexer")
    assert code == 0 and "Aucun cours indexé" in out
    (tmp_path / "cours" / "endocrino.txt").write_text(
        "Le pancréas endocrine sécrète l'insuline par les cellules bêta des îlots de Langerhans.", encoding="utf-8"
    )
    (tmp_path / "cours" / "casse.pdf").write_bytes(b"pas un pdf")
    code, out = run(capsys, "--dossier", str(tmp_path), "indexer")
    assert code == 0 and "1 fichier(s) lus" in out and "Non lu : casse.pdf" in out
    code, out = run(capsys, "--dossier", str(tmp_path), "chercher", "cellules bêta insuline")
    assert code == 0 and "1. endocrino.txt" in out and "Langerhans" in out


def test_tester_avec_moteur_factice(tmp_path, capsys, monkeypatch):
    image = QImage(800, 600, QImage.Format.Format_RGB32)
    image.fill(QColor("white"))
    path = tmp_path / "capture.png"
    image.save(str(path))
    monkeypatch.setattr(select, "make_backend", lambda cfg: FakeBackend())
    code, out = run(capsys, "--dossier", str(tmp_path), "tester", str(path), "--sans-cours")
    assert code == 0
    assert "7 : AC" in out and "connaissances, confiance haute" in out and "tu avais coché : AB" in out
    assert " 8 :" not in out and "Durées :" in out  # l'énoncé seul de la question 8 attend la capture suivante
    code, out = run(capsys, "--dossier", str(tmp_path), "tester", str(path), str(path), "--sans-cours")
    assert code == 0 and out.count("7 : AC") == 2 and "--- capture.png" in out

    monkeypatch.setattr(select, "make_backend", lambda cfg: (_ for _ in ()).throw(BackendError("Pas de connexion")))
    code, out = run(capsys, "--dossier", str(tmp_path), "tester", str(path))
    assert code == 1 and "Échec : Pas de connexion" in out
    code, out = run(capsys, "--dossier", str(tmp_path), "tester", str(tmp_path / "absent.png"))
    assert code == 1 and "illisible" in out


def test_diagnostic(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(select, "make_backend", lambda cfg: FakeBackend())
    code, out = run(capsys, "--dossier", str(tmp_path), "diagnostic")
    assert code == 0 and "Tout est en ordre" in out and "aucun cours indexé" in out
    (tmp_path / "config.toml").write_text('[raccourcis]\ncorriger = "ctrl+"\n', encoding="utf-8")
    monkeypatch.setattr(select, "make_backend", lambda cfg: (_ for _ in ()).throw(BackendError("Aucune connexion")))
    code, out = run(capsys, "--dossier", str(tmp_path), "diagnostic")
    assert code == 1 and "[!!]  Connexion à Claude : Aucune connexion" in out and "2 point(s) à corriger" in out
    (tmp_path / "config.toml").write_text("[affichage]\nmode = 'hologramme'\n", encoding="utf-8")
    code, out = run(capsys, "--dossier", str(tmp_path), "diagnostic")
    assert code == 1 and "affichage.mode" in out


def test_choix_du_moteur(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    cfg = Config(base_dir=tmp_path, ia=AIConfig(cli_commande="programme-absent-xyz"))
    with pytest.raises(BackendError, match="Aucune connexion"):
        select.make_backend(cfg)
    cfg.ia.backend = "api"
    with pytest.raises(BackendError, match="cle_api.txt"):
        select.make_backend(cfg)
    cfg.ia.backend = "claude_cli"
    assert select.make_backend(cfg).name == "claude_cli"
    (tmp_path / "cle_api.txt").write_text("sk-ant-test-123\n", encoding="utf-8")
    cfg.ia.backend = "auto"
    assert select.make_backend(cfg).name == "api"
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
