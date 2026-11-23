package fr.correcteur.annales

import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Color
import android.os.SystemClock
import android.util.Base64
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.fasterxml.jackson.databind.ObjectMapper
import fr.correcteur.annales.core.AnswerList
import fr.correcteur.annales.core.ClaudeClient
import fr.correcteur.annales.core.Modele
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import java.util.concurrent.CopyOnWriteArrayList

/**
 * Essais sur un vrai système Android (émulateur) : la bibliothèque de Claude tourne-t-elle,
 * le service capture-t-il l'écran, et l'affichage montre-t-il les lettres ?
 */
@RunWith(AndroidJUnit4::class)
class TabletteTest {
    private val instrumentation = InstrumentationRegistry.getInstrumentation()
    private val context = instrumentation.targetContext
    private lateinit var server: MockWebServer
    private val bodies = CopyOnWriteArrayList<String>()
    private val mapper = ObjectMapper()

    private val answer = """{"questions":[
        {"numero":"4","debut_visible":true,"complete":true,"type":"QRU","cocher":"vraies","enonce":"",
         "propositions":[{"lettre":"A","texte":"","vraie":false},{"lettre":"B","texte":"","vraie":true}],
         "reponse_libre":"","confiance":"haute"},
        {"numero":"5","debut_visible":true,"complete":true,"type":"QRM","cocher":"fausses","enonce":"",
         "propositions":[{"lettre":"A","texte":"","vraie":false},{"lettre":"B","texte":"","vraie":true},{"lettre":"C","texte":"","vraie":false}],
         "reponse_libre":"","confiance":"haute"}]}"""

    @Before
    fun setUp() {
        server = MockWebServer()
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                bodies += request.body.readUtf8()
                val message = mapOf(
                    "id" to "msg_1", "type" to "message", "role" to "assistant", "model" to "claude-opus-5-5",
                    "content" to listOf(mapOf("type" to "text", "text" to answer)),
                    "stop_reason" to "end_turn", "stop_sequence" to null,
                    "usage" to mapOf("input_tokens" to 5000, "output_tokens" to 300),
                )
                return MockResponse().setHeader("content-type", "application/json").setBody(mapper.writeValueAsString(message))
            }
        }
        server.start()
    }

    @After
    fun tearDown() {
        server.shutdown()
    }

    /**
     * Lance une commande et attend sa fin (fermer le canal trop tôt l'interromprait). Sans
     * FLAG_DONT_SUPPRESS_ACCESSIBILITY_SERVICES, l'outil de test mettrait en sourdine tous les
     * services d'accessibilité, dont celui qu'on veut essayer.
     */
    private fun shell(command: String): String {
        val automation = instrumentation.getUiAutomation(android.app.UiAutomation.FLAG_DONT_SUPPRESS_ACCESSIBILITY_SERVICES)
        val output = automation.executeShellCommand(command)
        return android.os.ParcelFileDescriptor.AutoCloseInputStream(output).use { it.readBytes().decodeToString() }
    }

    private fun <T> withDetail(block: () -> T): T = try {
        block()
    } catch (e: fr.correcteur.annales.core.CorrectionError) {
        throw AssertionError("${e.message} | ${e.detail}", e)
    }

    @Test
    fun bibliotheque_claude_sur_android() {
        val client = ClaudeClient("sk-ant-api03-test", Modele.OPUS, baseUrl = server.url("").toString().trimEnd('/'))
        val screen = withDetail { client.correct("iVBORw0KGgo=", null) }
        client.close()
        assertEquals(listOf("B", "AC"), screen.questions.map { it.lettres() })
    }

    @Test
    fun affichage_lisible() {
        val list = AnswerList()
        list.add(listOf(fr.correcteur.annales.core.Answer("4", "ACD")))
        val view = AnswerView(context, list) {}
        view.textSizePx = pointsToPx(context, 14)
        view.measure(0, 0)
        view.layout(0, 0, view.measuredWidth, view.measuredHeight)
        val bitmap = Bitmap.createBitmap(view.measuredWidth, view.measuredHeight, Bitmap.Config.ARGB_8888)
        view.draw(Canvas(bitmap))
        val pixels = IntArray(bitmap.width * bitmap.height).also { bitmap.getPixels(it, 0, bitmap.width, 0, 0, bitmap.width, bitmap.height) }
        assertTrue(pixels.any { Color.alpha(it) == 255 && Color.red(it) > 240 && Color.green(it) > 240 })
        assertTrue(pixels.any { Color.alpha(it) == 255 && Color.red(it) < 20 && Color.green(it) < 20 })
    }

    @Test
    fun capture_et_correction_de_bout_en_bout() {
        val prefs = Prefs(context)
        prefs.apiKey = "sk-ant-api03-test"
        prefs.apiBaseUrl = server.url("").toString().trimEnd('/')
        prefs.taille = 8
        shell("settings put secure enabled_accessibility_services ${context.packageName}/${CorrecteurService::class.java.name}")
        shell("settings put secure accessibility_enabled 1")
        val enabled = shell("settings get secure enabled_accessibility_services")

        val deadline = SystemClock.uptimeMillis() + 30_000
        while (CorrecteurService.instance == null && SystemClock.uptimeMillis() < deadline) SystemClock.sleep(200)
        val service = checkNotNull(CorrecteurService.instance) { "le service ne s'est pas lancé (réglage : $enabled)" }
        instrumentation.runOnMainSync { assertTrue(service.dotShown()) }

        instrumentation.runOnMainSync { service.capture() }
        var lines = emptyList<String>()
        val end = SystemClock.uptimeMillis() + 40_000
        while (SystemClock.uptimeMillis() < end) {
            instrumentation.runOnMainSync { lines = service.visibleLines() }
            if (lines.contains("4  B")) break
            SystemClock.sleep(250)
        }
        assertEquals("requêtes reçues : ${bodies.size}", listOf("4  B", "5  AC"), lines)

        // La capture envoyée est une vraie image PNG de l'écran.
        val body = mapper.readTree(bodies.last())
        val data = body["messages"][0]["content"][0]["source"]["data"].asText()
        val png = Base64.decode(data, Base64.DEFAULT)
        assertTrue(png.size > 1000)
        assertEquals(0x89.toByte(), png[0])
        assertEquals('P'.code.toByte(), png[1])
        instrumentation.runOnMainSync { assertTrue(service.dotShown()) }
        shell("settings put secure enabled_accessibility_services \"\"")
    }
}
