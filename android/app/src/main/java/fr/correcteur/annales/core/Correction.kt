package fr.correcteur.annales.core

import com.fasterxml.jackson.databind.JsonNode
import com.fasterxml.jackson.databind.ObjectMapper

/** Une proposition telle que Claude l'a lue et jugée. */
data class Proposition(
    val lettre: String,
    val texte: String,
    /** L'affirmation est-elle vraie, indépendamment de la consigne ? */
    val vraie: Boolean,
)

/** Une question de la capture, avec la correction de ses propositions. */
data class Question(
    val numero: String,
    /** false : la question a commencé sur l'écran précédent (on n'en voit que la fin). */
    val debutVisible: Boolean,
    /** false : la question continue sous le bord bas de l'écran. */
    val complete: Boolean,
    val type: String,
    /** La consigne demande-t-elle de cocher les propositions fausses ? */
    val cocherFausses: Boolean,
    /** Recopié seulement pour une question coupée en bas : la capture suivante en a besoin. */
    val enonce: String,
    val propositions: List<Proposition>,
    val reponseLibre: String,
    val confiance: String,
) {
    /** Lettres à cocher : les vraies, ou les fausses si la consigne le demande. */
    fun lettres(): String = lettersToTick(propositions.associate { normalizeLetter(it.lettre) to it.vraie }, cocherFausses)
}

data class Screen(val questions: List<Question>)

class CorrectionError(message: String, val detail: String = "") : Exception(message)

fun lettersToTick(verdicts: Map<String, Boolean>, cocherFausses: Boolean): String =
    verdicts.filter { (_, vraie) -> vraie != cocherFausses }.keys.sorted().joinToString("")

/** « Question 12 : », « 12. » ou « QCM 12 » -> « 12 » ; chaîne vide si aucun chiffre. */
fun normalizeNumber(numero: String): String {
    val digits = Regex("\\d+").findAll(numero).lastOrNull()?.value ?: return numero.trim()
    return digits.trimStart('0').ifEmpty { "0" }
}

fun normalizeLetter(lettre: String): String =
    lettre.firstOrNull { it.isLetter() }?.uppercaseChar()?.toString() ?: lettre.trim().uppercase()

private val mapper = ObjectMapper()

/** Lit la réponse JSON de Claude (format imposé par [Prompts.SCHEMA]). */
fun parseScreen(json: String): Screen {
    val start = json.indexOf('{')
    val end = json.lastIndexOf('}')
    if (start < 0 || end <= start) throw CorrectionError("Réponse de Claude illisible, réessaie.", json.take(300))
    val root: JsonNode = try {
        mapper.readTree(json.substring(start, end + 1))
    } catch (e: Exception) {
        throw CorrectionError("Réponse de Claude illisible, réessaie.", e.message ?: "")
    }
    val questions = root.path("questions").map { q ->
        Question(
            numero = normalizeNumber(q.path("numero").asText("")),
            debutVisible = q.path("debut_visible").asBoolean(true),
            complete = q.path("complete").asBoolean(true),
            type = q.path("type").asText("QRM"),
            cocherFausses = q.path("cocher").asText("vraies") == "fausses",
            enonce = q.path("enonce").asText(""),
            propositions = q.path("propositions").map { p ->
                Proposition(
                    lettre = normalizeLetter(p.path("lettre").asText("")),
                    texte = p.path("texte").asText(""),
                    vraie = p.path("vraie").asBoolean(false),
                )
            },
            reponseLibre = q.path("reponse_libre").asText("").trim(),
            confiance = q.path("confiance").asText("moyenne"),
        )
    }
    return Screen(questions)
}
