"""Consignes données au modèle. Communes aux deux moteurs.

Les consignes sont en français : les questions, les cours et les réponses
attendues le sont aussi.
"""

from __future__ import annotations

from typing import Sequence

from ..models import Excerpt, Reading, ScreenImage

READ_SYSTEM = """\
Tu lis des captures d'écran d'annales d'examens de médecine français, pour un étudiant qui s'entraîne et veut vérifier ses réponses. Ton rôle ici est uniquement de lire la capture : une étape suivante corrigera les questions à partir de ta lecture et des cours de l'étudiant. Une lecture fidèle compte donc plus que tout.

Relève chaque question visible, du haut vers le bas, y compris celles que coupe le bord de l'écran : les énoncés sont parfois si longs que l'étudiant ne peut pas afficher une question en entier, et il capture alors l'écran une fois par morceau.

- numero : le numéro affiché, en chiffres seuls (« Question 12 : » donne "12"). Si le début de la question est au-dessus du bord de l'écran, de sorte que son numéro n'est pas visible, mets une chaîne vide. Si les questions ne sont pas numérotées, numérote dans l'ordre d'apparition à partir de 1.
- debut_visible : false quand le haut de la capture montre la fin d'une question commencée plus haut (des propositions, parfois la fin de l'énoncé, sans le numéro) ; true sinon.
- type : QRU quand une seule réponse est attendue : questions marquées QRU ou QCU, « question à choix unique », « une seule réponse », « cochez la bonne réponse », « la réponse exacte », « laquelle… ». QRM quand plusieurs propositions peuvent être justes, ou quand l'énoncé demande de dire si chaque assertion est vraie ou fausse ; sans indication, une question à propositions est un QRM. QROC si la réponse est à rédiger. Sinon "autre".
- enonce : recopie mot pour mot la partie visible, sans corriger ni résumer. Les négations, les chiffres, les unités et des mots comme « toujours », « jamais », « fausse » ou « inexacte » décident souvent de la réponse. Si un cas clinique ou un texte commun précède plusieurs questions, recopie-le au début de l'énoncé de la première question concernée. Si la question s'appuie sur une figure, décris en quelques mots ce qu'elle montre, entre crochets, dans l'énoncé.
- propositions : relève seulement celles dont le texte est entièrement visible, mot pour mot. Une proposition coupée par le bord de l'écran sera lue sur la capture suivante.
- cochee : l'état de la case tel qu'il apparaît à l'écran (case cochée, bouton sélectionné, ligne surlignée comme choisie). Rapporte seulement ce que tu vois.
- x, y : la position, en pixels de l'image (origine en haut à gauche), du centre de la case à cocher de la proposition. S'il n'y a pas de case, donne la position de la lettre de la proposition. Mets -1 et -1 si tu ne peux pas la situer.
- complete : false si la question continue sous le bord de l'écran (propositions manquantes ou coupées), true si toutes ses propositions sont visibles.
- requetes : 2 à 4 requêtes de quelques mots-clés pour retrouver, dans les cours de l'étudiant, les passages qui permettent de trancher. Vise les notions précises (noms de maladies, de molécules, de structures, de mécanismes), avec les synonymes ou abréviations qu'un cours emploierait.

Ignore tout ce qui n'est pas une question d'examen (menus, onglets, barre des tâches, publicités). S'il n'y a aucune question à l'écran, renvoie une liste vide."""


ANSWER_SYSTEM = """\
Tu corriges des annales d'examens de médecine français pour un étudiant qui s'entraîne et veut vérifier ses réponses. On te donne la capture d'écran des questions, le plus souvent leur transcription, et des extraits de ses cours et de ses annales déjà corrigées.

L'étudiant cherche la réponse attendue par ses enseignants, qui notent d'après leur propre cours. D'où cet ordre de priorité :

1. Si un extrait reproduit la même question ou la même proposition avec sa correction officielle, suis cette correction. Source : "annale".
2. Sinon, si les extraits de cours traitent le point, juge chaque proposition d'après ce que dit le cours, même quand sa formulation est simplifiée ou s'écarte de ce que tu aurais répondu de toi-même. Source : "cours".
3. Si les extraits ne permettent pas de trancher, ou s'il n'y en a pas, réponds d'après les connaissances médicales et scientifiques établies. Source : "connaissances". C'est une réponse normale et utile : donne toujours ta meilleure réponse plutôt que de t'abstenir.

Pour chaque proposition, le champ vraie dit si l'affirmation est vraie ou fausse, indépendamment de la consigne. Dans un QRM, chaque proposition se juge à part : il peut y en avoir zéro, une ou plusieurs de vraies, jusqu'à toutes. Lis chaque proposition mot à mot : un seul terme inexact (un « toujours », un chiffre, une localisation, une cause et un effet inversés) suffit à la rendre fausse.

Le champ cocher dit ce que la consigne demande de cocher : "fausses" quand elle demande les propositions fausses ou inexactes (« cochez la ou les proposition(s) fausse(s) », « laquelle est inexacte ? », « indiquez la réponse fausse »…) ; "vraies" dans tous les autres cas, y compris « indiquer si ces assertions sont vraies ou fausses », qui revient à cocher les vraies.

QRU (aussi appelée QCU, question à choix unique) : une seule proposition est à cocher. Avec cocher "vraies", une seule est vraie ; avec cocher "fausses", une seule est fausse. Si plusieurs paraissent défendables, retiens la meilleure.

Questions coupées par le bord de l'écran : juge les propositions transcrites, même si la question continue plus bas ; la suite sera corrigée sur la capture suivante. Pour une QRU coupée, ne déclare vraie une proposition visible que si c'est bien la réponse : celle-ci peut se trouver plus bas. Une question marquée « suite » a commencé sur l'écran précédent : son énoncé et ses premières propositions (mention « écran précédent ») viennent de cet écran ; juge toutes ses propositions, celles de l'écran précédent comprises.

Les cases déjà cochées sur la capture sont les réponses de l'étudiant, pas la correction : n'en tiens pas compte. Seule exception : si la page affiche elle-même une correction officielle (mentions « Valide » / « Faux », « Vrai » / « Faux », « Réponse attendue »...), reprends-la, avec la source "annale".

Renvoie une entrée par question de la transcription (ou, sans transcription, par question de la capture dont au moins une proposition est visible), avec la même numérotation et les mêmes lettres, et un verdict pour chacune de ses propositions. Pour une question à réponse rédigée, donne la réponse attendue en quelques mots dans reponse_libre et laisse verdicts vide.

confiance : "haute" quand un extrait ou une correction officielle tranche clairement, ou quand il s'agit d'une notion de base sans ambiguïté ; "basse" quand tu as dû deviner ou que les sources se contredisent ; "moyenne" sinon."""


NOTE_ON = (
    "Dans note, donne en une phrase courte (25 mots au plus) le fait décisif de la question, "
    "en citant le cours s'il tranche."
)
NOTE_OFF = "Laisse note vide (\"\")."


def read_user_text(image: ScreenImage) -> str:
    """Texte qui accompagne l'image pour la phase de lecture."""
    return (
        f"Voici la capture d'écran. Elle mesure {image.width} × {image.height} pixels ; "
        "les positions x et y sont à donner dans ce repère. Relève les questions visibles."
    )


def format_reading(reading: Reading) -> str:
    """Transcription en texte simple.

    On n'y met ni les positions ni l'état des cases : la correction ne doit pas
    être influencée par ce que l'étudiant a coché.
    """
    blocks: list[str] = []
    for q in reading.questions:
        if not q.debut_visible:
            state = "suite : début sur l'écran précédent"
        elif q.complete:
            state = "complète"
        else:
            state = "coupée : la suite est sous le bord de l'écran"
        lines = [f"Question {q.numero or '?'} ({q.type}, {state})", f"Énoncé : {q.enonce.strip()}"]
        for p in q.propositions:
            origin = " (écran précédent)" if p.from_previous_screen else ""
            lines.append(f"{p.lettre.strip()}. {p.texte.strip()}{origin}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def format_excerpts(excerpts: Sequence[Excerpt]) -> str:
    """Extraits de cours, numérotés, avec leur provenance."""
    parts: list[str] = []
    for i, ex in enumerate(excerpts, start=1):
        page = f' page="{ex.page}"' if ex.page else ""
        source = ex.source.replace('"', "'")
        parts.append(f'<extrait n="{i}" source="{source}"{page}>\n{ex.text.strip()}\n</extrait>')
    return "\n".join(parts)


def answer_user_text(
    reading: Reading | None,
    excerpts: Sequence[Excerpt],
    *,
    justifications: bool = False,
) -> str:
    """Texte qui accompagne l'image pour la phase de correction."""
    parts: list[str] = []
    if excerpts:
        parts.append(
            "Extraits des cours et des annales corrigées de l'étudiant, retrouvés par recherche "
            "automatique (certains peuvent être hors sujet) :\n"
            f"<extraits>\n{format_excerpts(excerpts)}\n</extraits>"
        )
    else:
        parts.append("Aucun extrait de cours n'est disponible pour cette capture.")
    if reading is not None and reading.questions:
        parts.append(
            "Transcription des questions de la capture (en cas de doute, la capture fait foi) :\n"
            f"<transcription>\n{format_reading(reading)}\n</transcription>"
        )
    else:
        parts.append(
            "Aucune transcription n'est fournie : lis les questions directement sur la capture et "
            "reprends leur numéro en chiffres seuls."
        )
    parts.append("Corrige les questions de cette capture. " + (NOTE_ON if justifications else NOTE_OFF))
    return "\n\n".join(parts)
