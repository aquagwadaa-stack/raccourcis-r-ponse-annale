package fr.correcteur.annales.core

/** Réponse prête à afficher : « 4  ACD ». */
data class Answer(
    val numero: String,
    val lettres: String,
    val reponseLibre: String = "",
    /** Lettre -> affirmation vraie ? (pour compléter une question coupée au fil des captures) */
    val verdicts: Map<String, Boolean> = emptyMap(),
    val cocherFausses: Boolean = false,
) {
    fun texte(): String = when {
        lettres.isNotEmpty() -> lettres
        reponseLibre.isNotEmpty() -> reponseLibre
        else -> "aucune"
    }
}

/**
 * Mémoire d'une séance : la question laissée coupée en bas de la dernière capture (pour
 * corriger sa suite) et les réponses déjà données (pour les compléter). Elle s'oublie après
 * un long moment sans capture.
 */
class Session(
    private val maxAgeMs: Long = 20 * 60 * 1000L,
    private val clock: () -> Long = System::currentTimeMillis,
) {
    private var cut: Pair<Long, Question>? = null
    private val answers = mutableMapOf<String, Pair<Long, Answer>>()

    @Synchronized
    fun clear() {
        cut = null
        answers.clear()
    }

    /** Question coupée en bas de la capture précédente, si elle est récente. */
    @Synchronized
    fun previousCut(): Question? {
        val (stamp, question) = cut ?: return null
        return if (clock() - stamp <= maxAgeMs) question else null
    }

    /** Intègre une capture corrigée ; renvoie les réponses à afficher, complétées si besoin. */
    @Synchronized
    fun record(screen: Screen): List<Answer> {
        val now = clock()
        val earlier = previousCut()
        val result = mutableListOf<Answer>()
        for ((index, q) in screen.questions.withIndex()) {
            if (q.propositions.isEmpty() && q.reponseLibre.isEmpty()) continue // énoncé seul : rien à cocher encore
            // La suite d'une question coupée garde le numéro de la capture précédente.
            val numero = q.numero.ifEmpty { if (index == 0 && earlier != null) earlier.numero else "?" }
            var verdicts = q.propositions.associate { normalizeLetter(it.lettre) to it.vraie }
            val known = answers[numero]
            if (!q.debutVisible && known != null && now - known.first <= maxAgeMs) {
                verdicts = known.second.verdicts + verdicts
            }
            val answer = Answer(numero, lettersToTick(verdicts, q.cocherFausses), q.reponseLibre, verdicts, q.cocherFausses)
            answers[numero] = now to answer
            result += answer
        }
        // La dernière question, si elle continue sous l'écran, servira de contexte à la capture suivante.
        val last = screen.questions.lastOrNull()
        cut = if (last != null && !last.complete) {
            val numero = last.numero.ifEmpty { if (screen.questions.size == 1 && earlier != null) earlier.numero else "" }
            val merged = if (!last.debutVisible && earlier != null) {
                // Question à cheval sur trois écrans : on garde le début vu deux captures plus tôt.
                val seen = last.propositions.map { normalizeLetter(it.lettre) }.toSet()
                last.copy(
                    numero = numero,
                    enonce = earlier.enonce.ifEmpty { last.enonce },
                    propositions = earlier.propositions.filter { normalizeLetter(it.lettre) !in seen } + last.propositions,
                )
            } else last.copy(numero = numero)
            now to merged
        } else null
        return result
    }
}

/**
 * Ce que l'affichage montre : au plus [maxAnswers] réponses, les plus anciennes disparaissent,
 * mais jamais une réponse de la dernière capture.
 */
class AnswerList(var maxAnswers: Int = 2) {
    private data class Row(val answer: Answer, val order: Long)

    private val rows = linkedMapOf<String, Row>()
    private var counter = 0L
    private var latest = emptySet<String>()
    var pending = false
    var message: String? = null
    var messageIsError = false

    fun add(answers: List<Answer>) {
        pending = false
        message = if (answers.isEmpty()) "aucune question sur cet écran" else null
        messageIsError = false
        if (answers.isEmpty()) return
        for (a in answers) rows[a.numero] = Row(a, ++counter)
        latest = answers.map { it.numero }.toSet()
        val limit = maxOf(maxAnswers, latest.size)
        rows.entries.sortedBy { it.value.order }.dropLast(limit).forEach { rows.remove(it.key) }
    }

    fun error(text: String) {
        pending = false
        message = text
        messageIsError = true
    }

    fun clear() {
        rows.clear()
        latest = emptySet()
        message = null
        pending = false
    }

    fun isEmpty(): Boolean = rows.isEmpty() && message == null && !pending

    /** Lignes à afficher, ex. [("4", "ACD"), ("5", "C")] triées par numéro. */
    fun answerLines(): List<Pair<String, String>> =
        rows.values.map { it.answer }
            .sortedWith(compareBy({ it.numero.toIntOrNull() ?: Int.MAX_VALUE }, { it.numero }))
            .map { it.numero to it.texte() }
}

/** Taille de l'image envoyée : au plus 2576 pixels de côté et 3,75 mégapixels (limites de Claude). */
fun targetSize(width: Int, height: Int): Pair<Int, Int> {
    if (width <= 0 || height <= 0) return maxOf(width, 1) to maxOf(height, 1)
    val scale = minOf(1.0, 2576.0 / maxOf(width, height), Math.sqrt(3_750_000.0 / (width.toDouble() * height)))
    if (scale >= 1.0) return width to height
    return maxOf(1, (width * scale).toInt()) to maxOf(1, (height * scale).toInt())
}
