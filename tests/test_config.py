"""Réglages : lecture, mise à jour de l'ancien format, enregistrement depuis l'application."""

from __future__ import annotations

import subprocess
import tomllib
from pathlib import Path

import pytest

from correcteur.config import (
    BACKUP_FILENAME,
    CONFIG_VERSION,
    ConfigError,
    load_config,
    migrate_config,
    save_setting,
    set_config_value,
)

REPO = Path(__file__).resolve().parent.parent

V1_CONFIG = """\
[raccourcis]
corriger = "f8"         # capture l'écran et lance la correction
masquer  = "f9"

[affichage]
mode = "coches"        # choisi par l'étudiant
taille_police = 20
opacite = 0.92

[ia]
backend = "auto"
cli_modele_lecture = "haiku"
cli_modele = "opus"
option_disparue = 3

[cours]
dossier = "mes cours"
"""


def test_defauts_actuels(tmp_path):
    cfg = load_config(tmp_path)
    assert cfg.raccourcis.corriger == "!"
    assert (cfg.affichage.taille_police, cfg.affichage.reponses_max, cfg.affichage.disposition) == (4, 2, "colonne")


def test_exemple_valide_et_a_jour(tmp_path):
    example = (REPO / "config.exemple.toml").read_text(encoding="utf-8")
    assert tomllib.loads(example)["version"] == CONFIG_VERSION
    (tmp_path / "config.toml").write_text(example, encoding="utf-8")
    cfg = load_config(tmp_path)
    assert cfg.raccourcis.corriger == "!" and cfg.affichage.taille_police == 4


def test_migration_garde_les_choix_et_remplace_les_anciens_defauts(tmp_path):
    (tmp_path / "config.toml").write_text(V1_CONFIG, encoding="utf-8")
    message = migrate_config(tmp_path)
    assert message and "affichage.mode" in message and "ia.cli_modele" in message and "cours.dossier" in message
    cfg = load_config(tmp_path)
    assert cfg.raccourcis.corriger == "!"  # ancienne valeur par défaut : remplacée
    assert cfg.affichage.taille_police == 4 and cfg.affichage.opacite == 0.9
    assert cfg.affichage.mode == "coches" and cfg.ia.cli_modele == "opus" and cfg.cours.dossier == "mes cours"
    assert (tmp_path / BACKUP_FILENAME).read_text(encoding="utf-8") == V1_CONFIG
    text = (tmp_path / "config.toml").read_text(encoding="utf-8")
    assert "# capture l'écran" in text and "option_disparue" not in text
    assert migrate_config(tmp_path) is None  # déjà à jour


def test_migration_sans_fichier_ou_illisible(tmp_path):
    assert migrate_config(tmp_path) is None
    (tmp_path / "config.toml").write_text("[affichage\nmode = ", encoding="utf-8")
    assert migrate_config(tmp_path) is None
    with pytest.raises(ConfigError, match="illisible"):
        load_config(tmp_path)


def test_ecriture_d_une_valeur():
    text = '[affichage]\nmode = "lettres"   # commentaire # avec dièse\n\n[ia]\nbackend = "auto"\n'
    changed = set_config_value(text, "affichage", "mode", "coches")
    assert 'mode = "coches"    # commentaire # avec dièse' in changed
    added = set_config_value(changed, "affichage", "reponses_max", 3)
    assert added.index("reponses_max = 3") < added.index("[ia]")
    new_section = set_config_value(text, "raccourcis", "corriger", 'a"b\\c')
    assert tomllib.loads(new_section)["raccourcis"]["corriger"] == 'a"b\\c'
    hash_value = set_config_value('[raccourcis]\ncorriger = "#"  # dièse\n', "raccourcis", "corriger", "!")
    assert tomllib.loads(hash_value)["raccourcis"]["corriger"] == "!" and "# dièse" in hash_value
    assert tomllib.loads(set_config_value("", "affichage", "opacite", 0.8))["affichage"]["opacite"] == 0.8


def test_enregistrement_depuis_l_application(tmp_path):
    save_setting(tmp_path, "affichage", "mode", "les_deux")
    assert load_config(tmp_path).affichage.mode == "les_deux"


@pytest.mark.parametrize(
    "section, line, message",
    [
        ("affichage", 'disposition = "diagonale"', "disposition"),
        ("affichage", "reponses_max = 0", "reponses_max"),
        ("affichage", "taille_police = 0", "taille_police"),
    ],
)
def test_valeurs_refusees(tmp_path, section, line, message):
    (tmp_path / "config.toml").write_text(f"version = 2\n[{section}]\n{line}\n", encoding="utf-8")
    with pytest.raises(ConfigError, match=message):
        load_config(tmp_path)


def test_ancienne_config_du_depot_migre(tmp_path):
    """Le config.exemple.toml de la version précédente (copié chez l'étudiant) passe au format actuel."""
    try:
        old = subprocess.run(
            ["git", "-C", str(REPO), "show", "8af57a2:config.exemple.toml"],
            capture_output=True, text=True, check=True,
        ).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        pytest.skip("historique git indisponible")
    (tmp_path / "config.toml").write_text(old, encoding="utf-8")
    message = migrate_config(tmp_path)
    assert message and "conservés" not in message  # rien n'avait été modifié
    cfg = load_config(tmp_path)
    assert cfg.raccourcis.corriger == "!" and cfg.affichage.taille_police == 4
