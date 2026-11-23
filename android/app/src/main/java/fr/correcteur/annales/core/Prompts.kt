package fr.correcteur.annales.core

/**
 * Consignes données à Claude. Sur la tablette, lecture et correction se font en un seul
 * appel (il n'y a pas d'index de cours à interroger entre les deux) : c'est plus rapide.
 */
object Prompts {

    val SYSTEM = """
Tu corriges des annales d'examens de médecine français pour un étudiant qui s'entraîne et veut vérifier ses réponses. On te donne une capture d'écran de sa tablette.

Relève chaque question visible, du haut vers le bas, y compris celles que coupe le bord de l'écran : les énoncés sont parfois si longs que l'étudiant capture une question en plusieurs fois.

- numero : le numéro affiché, en chiffres seuls (« Question 12 : » donne "12"). Chaîne vide si le début de la question est au-dessus du bord de l'écran. Si les questions ne sont pas numérotées, numérote dans l'ordre à partir de 1.
- debut_visible : false quand le haut de la capture montre la fin d'une question commencée plus haut (des propositions, parfois la fin de l'énoncé, sans le numéro) ; true sinon.
- complete : false si la question continue sous le bord bas de l'écran (propositions manquantes ou coupées) ; true si sa fin est visible.
- type : QRU quand une seule réponse est attendue (QRU, QCU, « question à choix unique », « une seule réponse », « cochez la bonne réponse », « la réponse exacte », « laquelle… ») ; QRM quand plusieurs propositions peuvent être justes ou quand il faut dire si chaque assertion est vraie ou fausse (sans indication, une question à propositions est un QRM) ; QROC si la réponse est à rédiger ; sinon "autre".
- cocher : "fausses" quand la consigne demande les propositions fausses ou inexactes (« cochez la ou les proposition(s) fausse(s) », « laquelle est inexacte ? »…) ; "vraies" dans tous les autres cas, y compris « indiquer si ces assertions sont vraies ou fausses ».
- propositions : seulement celles dont le texte est entièrement visible ; une proposition coupée par le bord de l'écran sera corrigée sur la capture suivante. Pour chacune, vraie dit si l'affirmation est vraie ou fausse, indépendamment de la consigne.
- enonce et texte des propositions : recopie-les mot pour mot seulement pour une question qui continue sous le bord bas de l'écran (complete = false), car la capture suivante en aura besoin ; laisse-les vides ("") pour les autres questions.
- reponse_libre : pour une question à rédiger, la réponse attendue en quelques mots ; sinon "".
- confiance : "haute" quand la notion est claire et sans ambiguïté, "basse" quand tu as dû deviner, "moyenne" sinon.

Pour juger : si la page affiche elle-même une correction officielle (« Valide » / « Faux », « Réponse attendue »…), reprends-la. Sinon, réponds d'après les connaissances médicales et scientifiques établies, telles qu'un enseignant de faculté de médecine française les attend. Donne toujours ta meilleure réponse plutôt que de t'abstenir. Dans un QRM, chaque proposition se juge à part : il peut y en avoir zéro, une ou plusieurs de vraies. Lis chaque proposition mot à mot : un seul terme inexact (un « toujours », un chiffre, une localisation, une cause et un effet inversés) suffit à la rendre fausse.

QRU : une seule proposition est à cocher (une seule vraie avec cocher "vraies", une seule fausse avec cocher "fausses"). Pour une QRU coupée, ne déclare vraie une proposition visible que si c'est bien la réponse : celle-ci peut se trouver plus bas.

Les cases déjà cochées sont les réponses de l'étudiant, pas la correction : n'en tiens pas compte.

Ignore tout ce qui n'est pas une question d'examen (menus, onglets, barres d'outils). S'il n'y a aucune question à l'écran, renvoie une liste vide.
""".trim()

    /** Texte qui accompagne la capture ; ``previous`` est la question laissée coupée sur la capture d'avant. */
    fun userText(previous: Question?): String {
        if (previous == null) return "Voici la capture. Corrige les questions visibles."
        val numero = previous.numero.ifEmpty { "?" }
        val lines = mutableListOf(
            "La capture précédente se terminait par la question $numero, coupée en bas de l'écran.",
            "Énoncé : ${previous.enonce.ifEmpty { "(non recopié)" }}",
        )
        for (p in previous.propositions) {
            lines += "${p.lettre}. ${p.texte} (écran précédent)"
        }
        lines += ""
        lines += "Si le haut de cette capture en montre la suite, donne-lui le numéro $numero, " +
            "mets debut_visible à false et juge toutes ses propositions, y compris celles de l'écran " +
            "précédent listées ci-dessus. Corrige ensuite les autres questions visibles."
        return lines.joinToString("\n")
    }

    private fun obj(properties: Map<String, Any>, required: List<String> = properties.keys.toList()) = mapOf(
        "type" to "object",
        "properties" to properties,
        "required" to required,
        "additionalProperties" to false,
    )

    /** Format JSON imposé à la réponse (sortie structurée). */
    val SCHEMA: Map<String, Any> = obj(
        mapOf(
            "questions" to mapOf(
                "type" to "array",
                "items" to obj(
                    mapOf(
                        "numero" to mapOf("type" to "string"),
                        "debut_visible" to mapOf("type" to "boolean"),
                        "complete" to mapOf("type" to "boolean"),
                        "type" to mapOf("type" to "string", "enum" to listOf("QRM", "QRU", "QROC", "autre")),
                        "cocher" to mapOf("type" to "string", "enum" to listOf("vraies", "fausses")),
                        "enonce" to mapOf("type" to "string"),
                        "propositions" to mapOf(
                            "type" to "array",
                            "items" to obj(
                                mapOf(
                                    "lettre" to mapOf("type" to "string"),
                                    "texte" to mapOf("type" to "string"),
                                    "vraie" to mapOf("type" to "boolean"),
                                ),
                            ),
                        ),
                        "reponse_libre" to mapOf("type" to "string"),
                        "confiance" to mapOf("type" to "string", "enum" to listOf("haute", "moyenne", "basse")),
                    ),
                ),
            ),
        ),
    )
}
