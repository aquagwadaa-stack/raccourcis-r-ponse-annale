package fr.correcteur.annales.core

import com.anthropic.client.AnthropicClient
import com.anthropic.client.okhttp.AnthropicOkHttpClient
import com.anthropic.core.JsonValue
import com.anthropic.errors.AnthropicIoException
import com.anthropic.errors.AnthropicServiceException
import com.anthropic.errors.BadRequestException
import com.anthropic.errors.InternalServerException
import com.anthropic.errors.NotFoundException
import com.anthropic.errors.OverloadedException
import com.anthropic.errors.PermissionDeniedException
import com.anthropic.errors.RateLimitException
import com.anthropic.errors.UnauthorizedException
import com.anthropic.models.beta.messages.BetaBase64ImageSource
import com.anthropic.models.beta.messages.BetaContentBlockParam
import com.anthropic.models.beta.messages.BetaFallbacksParam
import com.anthropic.models.beta.messages.BetaImageBlockParam
import com.anthropic.models.beta.messages.BetaJsonOutputFormat
import com.anthropic.models.beta.messages.BetaOutputConfig
import com.anthropic.models.beta.messages.BetaStopReason
import com.anthropic.models.beta.messages.MessageCreateParams
import java.time.Duration

/** Modèles proposés dans les réglages. */
enum class Modele(val id: String, val libelle: String) {
    OPUS("claude-opus-5-5", "Opus 5.5 — le plus fiable"),
    SONNET("claude-sonnet-5-5", "Sonnet 5.5 — plus rapide, moins cher"),
    HAIKU("claude-haiku-5-5", "Haiku 5.5 — le plus rapide, le moins cher"),
    ;

    /** Repli côté serveur si un filtre de sécurité refuse la capture (le vocabulaire médical en déclenche parfois). */
    val repli: Boolean get() = this != HAIKU // Haiku 5.5 n'a pas de repli côté serveur

    companion object {
        fun from(id: String?): Modele = entries.firstOrNull { it.id == id } ?: OPUS
    }
}

const val FALLBACK_BETA = "server-side-fallback-2026-07-01"

/** Appel à l'API Anthropic avec la clé de l'étudiant. */
class ClaudeClient(
    apiKey: String,
    private val modele: Modele = Modele.OPUS,
    private val effort: BetaOutputConfig.Effort = BetaOutputConfig.Effort.MEDIUM,
    baseUrl: String? = null,
    timeout: Duration = Duration.ofSeconds(120),
) {
    private val client: AnthropicClient = AnthropicOkHttpClient.builder()
        .apiKey(apiKey.trim())
        // En-tête documenté pour les clés API ; la bibliothèque n'ajoute alors pas d'en-tête Authorization.
        .putHeader("x-api-key", apiKey.trim())
        .apply { if (baseUrl != null) baseUrl(baseUrl) }
        .timeout(timeout)
        .maxRetries(2)
        .build()

    fun buildRequest(pngBase64: String, previous: Question?): MessageCreateParams {
        val image = BetaImageBlockParam.builder()
            .source(
                BetaBase64ImageSource.builder()
                    .data(pngBase64)
                    .mediaType(BetaBase64ImageSource.MediaType.IMAGE_PNG)
                    .build(),
            )
            .build()
        val schema = BetaJsonOutputFormat.Schema.builder()
            .putAllAdditionalProperties(Prompts.SCHEMA.mapValues { JsonValue.from(it.value) })
            .build()
        val builder = MessageCreateParams.builder()
            .model(modele.id)
            .maxTokens(16_000L)
            .system(Prompts.SYSTEM)
            .addUserMessageOfContentBlockParams(
                listOf(BetaContentBlockParam.ofImage(image), BetaContentBlockParam.ofText(Prompts.userText(previous))),
            )
            .outputConfig(
                BetaOutputConfig.builder()
                    .effort(effort)
                    .format(BetaJsonOutputFormat.builder().schema(schema).build())
                    .build(),
            )
        if (modele.repli) {
            builder.addBeta(FALLBACK_BETA).fallbacks(BetaFallbacksParam.ofDefault())
        }
        return builder.build()
    }

    /** Corrige une capture. Lève [CorrectionError] avec un message court en français. */
    fun correct(pngBase64: String, previous: Question?): Screen {
        val message = try {
            client.beta().messages().create(buildRequest(pngBase64, previous))
        } catch (e: Exception) {
            throw translate(e)
        }
        when (message.stopReason().orElse(null)) {
            BetaStopReason.REFUSAL -> throw CorrectionError(
                "Claude a refusé cette capture. Réessaie avec moins de questions à l'écran.",
                message.stopDetails().map { it.toString() }.orElse(""),
            )
            BetaStopReason.MAX_TOKENS -> throw CorrectionError("Réponse trop longue : capture moins de questions à la fois.")
            else -> Unit
        }
        val text = message.content().mapNotNull { block -> block.text().orElse(null)?.text() }.joinToString("")
        return parseScreen(text)
    }

    /** Vérifie la clé et le modèle ; renvoie une ligne d'état. */
    fun check(): String {
        try {
            val info = client.models().retrieve(modele.id)
            return "Clé valide : ${info.displayName()} disponible."
        } catch (e: Exception) {
            throw translate(e)
        }
    }

    fun close() = client.close()

    private fun translate(e: Exception): CorrectionError = when (e) {
        is CorrectionError -> e
        is UnauthorizedException -> CorrectionError("Clé API refusée : vérifie-la dans l'appli Correcteur.", e.message ?: "")
        is PermissionDeniedException -> CorrectionError("Cette clé API n'a pas accès à ce modèle.", e.message ?: "")
        is NotFoundException -> CorrectionError("Modèle introuvable : choisis-en un autre dans l'appli.", e.message ?: "")
        is RateLimitException -> CorrectionError("Trop de demandes d'un coup : attends quelques secondes.", e.message ?: "")
        is OverloadedException, is InternalServerException ->
            CorrectionError("Claude est surchargé en ce moment : réessaie dans un instant.", e.message ?: "")
        is BadRequestException ->
            if ((e.message ?: "").contains("credit", ignoreCase = true)) {
                CorrectionError("Plus de crédit sur ton compte API Anthropic : recharge-le sur console.anthropic.com.", e.message ?: "")
            } else {
                CorrectionError("Requête refusée par Claude (détail dans l'appli).", e.message ?: "")
            }
        is AnthropicServiceException -> CorrectionError("Erreur de Claude (${e.statusCode()}) : réessaie.", e.message ?: "")
        is AnthropicIoException -> CorrectionError("Pas de connexion Internet, ou Claude ne répond pas.", e.message ?: "")
        else -> CorrectionError("Erreur inattendue : réessaie.", "${e.javaClass.simpleName}: ${e.message}")
    }
}
