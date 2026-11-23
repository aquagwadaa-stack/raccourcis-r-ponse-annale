package fr.correcteur.annales.core

import com.fasterxml.jackson.databind.ObjectMapper
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Test
import java.time.Duration

/** La requête réellement envoyée par la bibliothèque officielle, et le traitement des réponses. */
class ClaudeClientTest {
    private lateinit var server: MockWebServer
    private val mapper = ObjectMapper()

    @Before
    fun start() {
        server = MockWebServer()
        server.start()
    }

    @After
    fun stop() {
        server.shutdown()
    }

    private fun client(modele: Modele = Modele.OPUS) =
        ClaudeClient("sk-ant-api03-test", modele, baseUrl = server.url("").toString().trimEnd('/'), timeout = Duration.ofSeconds(10))

    private fun message(text: String, stopReason: String = "end_turn") = MockResponse()
        .setHeader("content-type", "application/json")
        .setBody(
            mapper.writeValueAsString(
                mapOf(
                    "id" to "msg_1",
                    "type" to "message",
                    "role" to "assistant",
                    "model" to "claude-opus-5-5",
                    "content" to listOf(mapOf("type" to "text", "text" to text)),
                    "stop_reason" to stopReason,
                    "stop_sequence" to null,
                    "usage" to mapOf("input_tokens" to 5000, "output_tokens" to 300),
                ),
            ),
        )

    private val answer = """{"questions":[{"numero":"4","debut_visible":true,"complete":true,"type":"QRU","cocher":"vraies","enonce":"","propositions":[{"lettre":"A","texte":"","vraie":false},{"lettre":"B","texte":"","vraie":true}],"reponse_libre":"","confiance":"haute"}]}"""

    @Test
    fun requete_opus_avec_repli_et_format_impose() {
        server.enqueue(message(answer))
        val previous = Question("3", true, false, "QRM", false, "Énoncé coupé", listOf(Proposition("A", "texte A", true)), "", "haute")
        val screen = client().correct("iVBORw0KGgo=", previous)
        assertEquals("B", screen.questions.single().lettres())

        val request = server.takeRequest()
        assertTrue(request.path!!.startsWith("/v1/messages"))
        assertEquals("sk-ant-api03-test", request.getHeader("x-api-key"))
        assertNull(request.getHeader("Authorization"))
        assertTrue(request.getHeader("anthropic-beta")!!.contains(FALLBACK_BETA))
        val body = mapper.readTree(request.body.readUtf8())
        assertEquals("claude-opus-5-5", body["model"].asText())
        assertEquals(16000, body["max_tokens"].asInt())
        assertEquals(Prompts.SYSTEM, body["system"].asText())
        assertEquals("default", body["fallbacks"].asText())
        val content = body["messages"][0]["content"]
        assertEquals("image", content[0]["type"].asText())
        assertEquals("image/png", content[0]["source"]["media_type"].asText())
        assertEquals("iVBORw0KGgo=", content[0]["source"]["data"].asText())
        assertTrue(content[1]["text"].asText().contains("question 3, coupée"))
        assertEquals("medium", body["output_config"]["effort"].asText())
        assertEquals("json_schema", body["output_config"]["format"]["type"].asText())
        assertTrue(body["output_config"]["format"]["schema"]["properties"].has("questions"))
        for (forbidden in listOf("temperature", "top_p", "top_k", "thinking")) assertFalse(body.has(forbidden))
    }

    @Test
    fun haiku_sans_repli() {
        server.enqueue(message(answer))
        client(Modele.HAIKU).correct("AAAA", null)
        val request = server.takeRequest()
        val body = mapper.readTree(request.body.readUtf8())
        assertEquals("claude-haiku-5-5", body["model"].asText())
        assertFalse(body.has("fallbacks"))
        assertFalse((request.getHeader("anthropic-beta") ?: "").contains(FALLBACK_BETA))
    }

    private fun expectError(response: MockResponse, expected: String) {
        // Toujours la même réponse, quel que soit le nombre de nouveaux essais de la bibliothèque.
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest) = response
        }
        try {
            client().correct("AAAA", null)
            fail("une erreur était attendue")
        } catch (e: CorrectionError) {
            assertTrue("« ${e.message} » devrait contenir « $expected »", e.message!!.contains(expected))
        }
    }

    private fun apiError(status: Int, type: String, text: String) = MockResponse()
        .setResponseCode(status)
        .setHeader("content-type", "application/json")
        .setBody("""{"type":"error","error":{"type":"$type","message":"$text"}}""")

    @Test
    fun erreurs_traduites() {
        expectError(apiError(401, "authentication_error", "invalid x-api-key"), "Clé API refusée")
        expectError(apiError(400, "invalid_request_error", "Your credit balance is too low"), "Plus de crédit")
        expectError(apiError(404, "not_found_error", "model: nope"), "Modèle introuvable")
        expectError(message(answer, stopReason = "refusal"), "refusé cette capture")
        expectError(message("""{"questions": [""", stopReason = "max_tokens"), "trop longue")
        expectError(message("pas du JSON"), "illisible")
    }

    @Test
    fun verification_de_la_cle() {
        server.enqueue(
            MockResponse().setHeader("content-type", "application/json").setBody(
                """{"id":"claude-opus-5-5","type":"model","display_name":"Claude Opus 5.5","created_at":"2026-01-01T00:00:00Z"}""",
            ),
        )
        assertEquals("Clé valide : Claude Opus 5.5 disponible.", client().check())
        assertTrue(server.takeRequest().path!!.startsWith("/v1/models/claude-opus-5-5"))
    }

    @Test
    fun modele_par_defaut() {
        assertEquals(Modele.OPUS, Modele.from(null))
        assertEquals(Modele.HAIKU, Modele.from("claude-haiku-5-5"))
        assertNull(Modele.entries.firstOrNull { it.id == "inconnu" })
    }
}
