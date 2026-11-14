"""Extraction, indexation et recherche dans les cours."""

from __future__ import annotations

import os
import threading
import time

import pymupdf
import pytest
from docx import Document
from pptx import Presentation
from pptx.util import Inches

from correcteur.courses import CourseIndex, extract_file
from correcteur.courses.extract import Page
from correcteur.courses.index import CHUNK_MAX_CHARS, chunk_pages, fts_query, query_terms

QT_PAGES = [
    "Syndrome du QT long congénital. Le syndrome du QT long (SQTL) est une canalopathie. "
    "L'intervalle QT correspond à la dépolarisation et à la repolarisation des ventricules. "
    "La période réfractaire absolue du cardiomyocyte correspond à la période d'ouverture de "
    "ses canaux potassiques selon le cours du professeur.",
    "Forme acquise du QT long : elle est généralement secondaire à la prescription de certains "
    "médicaments (antiarythmiques, macrolides, neuroleptiques) ou à une hypokaliémie.",
    "Classification de Vaughan-Williams des anti-arythmiques. Classe I : bloqueurs des canaux "
    "sodiques. Classe II : bêta-bloquants. Classe III : bloqueurs des canaux potassiques. "
    "Classe IV : inhibiteurs calciques.",
]
GS_SLIDES = [
    ("Protéines G hétérotrimériques", "Trois sous-unités : alpha, bêta et gamma. La sous-unité alpha est une GTPase."),
    ("Protéine Gs", "La protéine Gs active l'adénylate cyclase, qui produit de l'AMPc et active la PKA."),
    ("Protéine Gq", "La protéine Gq active la phospholipase C : IP3 et DAG, sortie de Ca2+ du réticulum endoplasmique."),
]
FASL_PARAGRAPHS = [
    "Apoptose par la voie extrinsèque. FasL est un ligand membranaire de la famille du TNF, "
    "il se fixe sur le récepteur Fas (CD95) et déclenche l'activation de la caspase 8.",
    "Une anomalie de FasL ou de Fas entraîne un syndrome lymphoprolifératif auto-immun (ALPS).",
]
ANNALE = (
    "Annale 2023 corrigée. Question 7. Concernant la protéine Gs. "
    "A. Elle est composée de 4 monomères : Faux. B. Elle active la phospholipase C : Faux. "
    "C. Elle active l'adénylate cyclase : Valide."
)
DISTRACTORS = [
    "Anatomie du membre supérieur : l'humérus s'articule avec la scapula au niveau de l'articulation gléno-humérale.",
    "Histologie du tissu conjonctif : fibroblastes, fibres de collagène et substance fondamentale.",
    "Biostatistiques : la médiane partage la distribution en deux effectifs égaux, l'écart-type mesure la dispersion.",
]


def make_pdf(path, pages):
    doc = pymupdf.open()
    for text in pages:
        page = doc.new_page()
        page.insert_textbox(pymupdf.Rect(50, 50, 545, 790), text, fontsize=11)
    doc.save(path)
    doc.close()


def make_pptx(path, slides):
    presentation = Presentation()
    for title, body in slides:
        slide = presentation.slides.add_slide(presentation.slide_layouts[5])
        slide.shapes.title.text = title
        box = slide.shapes.add_textbox(Inches(1), Inches(2), Inches(8), Inches(3))
        box.text_frame.text = body
    presentation.slides[0].notes_slide.notes_text_frame.text = "Rappel oral : le GTP est hydrolysé en GDP."
    presentation.save(path)


def make_docx(path, paragraphs):
    document = Document()
    for paragraph in paragraphs:
        document.add_paragraph(paragraph)
    document.save(path)


@pytest.fixture()
def corpus(tmp_path):
    courses = tmp_path / "cours"
    (courses / "UE2").mkdir(parents=True)
    make_pdf(courses / "Cardio - QT long.pdf", QT_PAGES)
    make_pptx(courses / "UE2" / "Signalisation proteines G.pptx", GS_SLIDES)
    make_docx(courses / "Apoptose.docx", FASL_PARAGRAPHS)
    make_pdf(courses / "Divers.pdf", DISTRACTORS)
    (courses / "annale_2023_corrigee.txt").write_text(ANNALE, encoding="utf-8")
    (courses / "notes.xyz").write_text("ignoré", encoding="utf-8")
    index = CourseIndex(tmp_path / "index")
    report = index.build(courses)
    return courses, index, report


def test_extraction_des_formats(corpus):
    courses, _index, _report = corpus
    pdf = extract_file(courses / "Cardio - QT long.pdf")
    assert [p.number for p in pdf.pages] == [1, 2, 3] and "canalopathie" in pdf.pages[0].text
    pptx = extract_file(courses / "UE2" / "Signalisation proteines G.pptx")
    assert "adénylate cyclase" in pptx.pages[1].text
    assert "hydrolysé" in pptx.pages[0].text  # notes de l'orateur
    docx = extract_file(courses / "Apoptose.docx")
    assert "caspase 8" in docx.pages[0].text
    assert extract_file(courses / "notes.xyz").error


def test_fichier_corrompu_et_page_sans_texte(tmp_path):
    bad = tmp_path / "casse.pdf"
    bad.write_bytes(b"pas un pdf")
    assert "illisible" in extract_file(bad).error
    blank = tmp_path / "scan.pdf"
    doc = pymupdf.open()
    doc.new_page()
    page = doc.new_page()
    page.insert_text((72, 72), "Une seule page contient vraiment du texte exploitable ici.")
    doc.save(blank)
    doc.close()
    result = extract_file(blank)
    assert result.empty_pages == 1 and [p.number for p in result.pages] == [2]


def test_rapport_de_construction(corpus):
    _courses, index, report = corpus
    assert report.indexed == 5 and report.failed == [] and report.chunks > 0
    assert not index.is_empty()
    stats = index.stats()
    assert stats.files == 5 and stats.chunks == report.chunks


@pytest.mark.parametrize(
    "query, expected_source, expected_text",
    [
        ("syndrome QT long congénital canaux potassiques période réfractaire", "Cardio - QT long.pdf", "réfractaire"),
        ("anti-arythmique classe I canal sodique", "Cardio - QT long.pdf", "Vaughan"),
        ("forme acquise QT long medicaments", "Cardio - QT long.pdf", "secondaire"),
        ("proteine Gs adenylate cyclase AMPc", "UE2/Signalisation proteines G.pptx", "adénylate"),
        ("phospholipase C Ca2+ réticulum", "UE2/Signalisation proteines G.pptx", "phospholipase"),
        ("FasL ligand récepteur Fas apoptose", "Apoptose.docx", "ligand"),
        # style « énoncé + proposition », avec les formules d'énoncé
        (
            "Indiquer si ces assertions concernant les anomalies de FasL sont vraies ou fausses FasL est un récepteur",
            "Apoptose.docx",
            "FasL",
        ),
        (
            "Indiquer si ces assertions concernant le syndrome de QT long (SQTL) congénital, sont vraies ou "
            "fausses La classe I d'anti-arythmiques cardiaques regroupe des bloqueurs de canaux sodiques",
            "Cardio - QT long.pdf",
            "sodiques",
        ),
    ],
)
def test_pertinence(corpus, query, expected_source, expected_text):
    _courses, index, _report = corpus
    results = index.search(query, limit=3)
    assert results, f"aucun résultat pour {query!r}"
    assert any(r.source == expected_source and expected_text in r.text for r in results), [
        (r.source, r.text[:60]) for r in results
    ]
    assert results[0].source == expected_source
    assert results[0].score >= results[-1].score and results[0].chunk_id > 0


def test_annale_corrigee_retrouvee(corpus):
    _courses, index, _report = corpus
    results = index.search("protéine Gs composée de 4 monomères", limit=3)
    assert any(r.source == "annale_2023_corrigee.txt" for r in results)


def test_requetes_bizarres_sans_erreur(corpus):
    _courses, index, _report = corpus
    for query in ["", "   ", "le la les de", '"', "AND OR NOT", "(Ca2+) : * ^ NEAR/3", "a" * 5000, "été – « test » ?"]:
        assert isinstance(index.search(query), list)
    assert index.search("") == [] and index.search("le la les") == []


def test_termes_de_requete():
    terms = dict(query_terms("Les canaux potassiques du QT long et la protéine Gs, IgE, pH 7"))
    assert terms == {"cana": True, "potass": True, "qt": False, "long": False, "protein": True, "gs": False, "ige": False, "ph": False, "7": False}
    assert fts_query("assertions vraies ou fausses") == ""
    assert '"cana"*' in fts_query("canaux")


def test_mise_a_jour_incrementale(corpus):
    courses, index, _report = corpus
    again = index.build(courses)
    assert (again.indexed, again.unchanged, again.removed) == (0, 5, 0)

    (courses / "Divers.pdf").unlink()
    path = courses / "annale_2023_corrigee.txt"
    path.write_text(ANNALE + " Question 8. La néphrine est une protéine du podocyte : Valide.", encoding="utf-8")
    os.utime(path, (time.time() + 5, time.time() + 5))
    make_docx(courses / "Nouveau.docx", ["La maladie de Berger est une néphropathie à dépôts mésangiaux d'IgA."])
    progress = []
    update = index.build(courses, progress=progress.append)
    assert (update.indexed, update.unchanged, update.removed) == (2, 3, 1)
    assert any("Retiré" in line for line in progress)
    assert index.search("humérus scapula") == []
    assert index.search("néphrine podocyte")[0].source == "annale_2023_corrigee.txt"
    assert index.search("maladie de Berger IgA")[0].source == "Nouveau.docx"


def test_fichier_illisible_n_interrompt_pas(tmp_path):
    courses = tmp_path / "cours"
    courses.mkdir()
    (courses / "casse.pdf").write_bytes(b"%PDF-1.4 tronque")
    (courses / "bon.txt").write_text("Le pancréas sécrète l'insuline par les cellules bêta des îlots.", encoding="utf-8")
    index = CourseIndex(tmp_path / "index")
    report = index.build(courses)
    assert report.indexed == 1 and len(report.failed) == 1 and report.failed[0][0] == "casse.pdf"
    assert index.search("insuline cellules beta")[0].source == "bon.txt"
    assert index.stats().failed_files == 1


def test_index_absent(tmp_path):
    index = CourseIndex(tmp_path / "rien")
    assert index.is_empty() and index.search("coeur") == []
    assert index.build(tmp_path / "dossier_inexistant").chunks == 0


def test_decoupage_en_passages():
    long_text = " ".join(f"Phrase numéro {i} sur la physiologie rénale." for i in range(200))
    pages = [Page(1, "Titre court"), Page(2, "Autre diapositive brève"), Page(3, long_text), Page(4, "Fin")]
    chunks = chunk_pages(pages)
    assert chunks[0] == (1, "Titre court\nAutre diapositive brève")
    assert all(len(text) <= CHUNK_MAX_CHARS for _page, text in chunks)
    assert sum(1 for page, _ in chunks if page == 3) >= 4
    assert "Phrase numéro 199" in "".join(text for _page, text in chunks)


def test_recherche_depuis_plusieurs_fils(corpus):
    _courses, index, _report = corpus
    errors, hits = [], []

    def work():
        try:
            for _ in range(20):
                hits.append(index.search("protéine Gs adénylate cyclase")[0].source)
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=work) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors and len(hits) == 160 and set(hits) == {"UE2/Signalisation proteines G.pptx"}


def test_vitesse_sur_gros_index(tmp_path):
    import random

    rng = random.Random(1)
    words = [f"terme{i}" for i in range(4000)] + "coeur rein foie canal potassium sodium proteine recepteur".split()
    index = CourseIndex(tmp_path / "index")
    conn = index._connect()
    conn.execute("INSERT INTO files(path, size, mtime_ns, pages, empty_pages, error) VALUES('gros.pdf', 1, 1, 1, 0, '')")
    conn.executemany(
        "INSERT INTO chunks(text, title, file_id, page) VALUES(?,?,1,?)",
        [(" ".join(rng.choices(words, k=180)), "gros", i) for i in range(20_000)],
    )
    conn.commit()
    query = (
        "Indiquer si ces assertions concernant le syndrome du QT long sont vraies : la période réfractaire "
        "absolue du cardiomyocyte correspond à l'ouverture des canaux potassiques et sodiques du coeur"
    )
    index.search(query)
    start = time.perf_counter()
    for _ in range(10):
        assert index.search(query, limit=5)
    per_query = (time.perf_counter() - start) / 10
    print(f"\nrecherche sur 20 000 passages : {per_query * 1000:.1f} ms")
    assert per_query < 0.5
