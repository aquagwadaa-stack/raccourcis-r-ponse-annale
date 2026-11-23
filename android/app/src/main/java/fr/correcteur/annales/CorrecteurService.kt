package fr.correcteur.annales

import android.accessibilityservice.AccessibilityService
import android.content.Intent
import android.graphics.Bitmap
import android.graphics.PixelFormat
import android.os.Handler
import android.os.Looper
import android.util.Base64
import android.util.Log
import android.util.TypedValue
import android.view.Display
import android.view.Gravity
import android.view.View
import android.view.WindowManager
import android.view.accessibility.AccessibilityEvent
import fr.correcteur.annales.core.AnswerList
import fr.correcteur.annales.core.ClaudeClient
import fr.correcteur.annales.core.CorrectionError
import fr.correcteur.annales.core.Session
import fr.correcteur.annales.core.targetSize
import java.io.ByteArrayOutputStream
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors

/**
 * Le correcteur sur la tablette. En tant que service d'accessibilité, il peut capturer
 * l'écran (Android 11 et plus) et poser par-dessus les autres applis un petit point et
 * l'affichage des réponses, sans autre autorisation.
 */
class CorrecteurService : AccessibilityService() {
    private lateinit var wm: WindowManager
    private lateinit var prefs: Prefs
    private val main = Handler(Looper.getMainLooper())
    private var worker: ExecutorService = Executors.newSingleThreadExecutor()
    private val list = AnswerList()
    private val session = Session()
    private var dot: DotView? = null
    private var dotParams: WindowManager.LayoutParams? = null
    private var answers: AnswerView? = null
    private var answersParams: WindowManager.LayoutParams? = null
    private var busy = false
    private var client: Pair<String, ClaudeClient>? = null

    override fun onServiceConnected() {
        super.onServiceConnected()
        prefs = Prefs(this)
        wm = getSystemService(WINDOW_SERVICE) as WindowManager
        if (worker.isShutdown) worker = Executors.newSingleThreadExecutor()
        addAnswers()
        addDot()
        instance = this
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) = Unit

    override fun onInterrupt() = Unit

    override fun onUnbind(intent: Intent?): Boolean {
        cleanup()
        return super.onUnbind(intent)
    }

    override fun onDestroy() {
        cleanup()
        super.onDestroy()
    }

    private fun cleanup() {
        if (instance === this) instance = null
        for (view in listOfNotNull(dot, answers)) runCatching { wm.removeView(view) }
        dot = null
        answers = null
        worker.shutdownNow()
        client?.second?.close()
        client = null
    }

    private fun dp(value: Int): Int =
        TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, value.toFloat(), resources.displayMetrics).toInt()

    private fun overlayParams() = WindowManager.LayoutParams(
        WindowManager.LayoutParams.WRAP_CONTENT,
        WindowManager.LayoutParams.WRAP_CONTENT,
        WindowManager.LayoutParams.TYPE_ACCESSIBILITY_OVERLAY,
        // Jamais le clavier ni les touches hors de nos petites zones : l'appli dessous reste utilisable.
        WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL,
        PixelFormat.TRANSLUCENT,
    )

    private fun addDot() {
        val params = overlayParams().apply { gravity = Gravity.TOP or Gravity.START }
        val metrics = resources.displayMetrics
        params.x = prefs.pointX.takeIf { it >= 0 } ?: (metrics.widthPixels - dp(56))
        params.y = prefs.pointY.takeIf { it >= 0 } ?: (metrics.heightPixels / 2)
        val view = DotView(
            this,
            onTap = ::capture,
            onMove = { dx, dy ->
                params.x = (params.x + dx).coerceIn(0, metrics.widthPixels - dp(20))
                params.y = (params.y + dy).coerceIn(0, metrics.heightPixels - dp(20))
                dot?.let { wm.updateViewLayout(it, params) }
            },
            onMoved = {
                prefs.pointX = params.x
                prefs.pointY = params.y
            },
        )
        wm.addView(view, params)
        dot = view
        dotParams = params
    }

    private fun addAnswers() {
        val params = overlayParams()
        placeAnswers(params)
        val view = AnswerView(this, list) { closeAnswers() }
        view.textSizePx = pointsToPx(this, prefs.taille)
        view.visibility = View.GONE
        wm.addView(view, params)
        answers = view
        answersParams = params
    }

    private fun placeAnswers(params: WindowManager.LayoutParams) {
        val coin = prefs.coin
        params.gravity = (if (coin.startsWith("haut")) Gravity.TOP else Gravity.BOTTOM) or
            (if (coin.endsWith("gauche")) Gravity.START else Gravity.END)
        params.x = dp(12)
        params.y = dp(12)
    }

    /** Applique les réglages changés dans l'appli (taille, coin). */
    fun refreshSettings() {
        val view = answers ?: return
        val params = answersParams ?: return
        view.textSizePx = pointsToPx(this, prefs.taille)
        placeAnswers(params)
        wm.updateViewLayout(view, params)
        render()
    }

    private fun render() {
        val view = answers ?: return
        view.visibility = if (list.isEmpty()) View.GONE else View.VISIBLE
        view.requestLayout()
        view.invalidate()
        dot?.busy = busy
    }

    private fun closeAnswers() {
        list.clear()
        render()
    }

    /** Touche sur le point : capture l'écran et lance la correction. */
    fun capture() {
        if (busy) return
        val key = prefs.apiKey
        if (key.isBlank()) {
            list.error("Ajoute ta clé API dans l'appli Correcteur.")
            render()
            return
        }
        busy = true
        // Ni le point ni les réponses ne doivent figurer sur la capture.
        dot?.visibility = View.INVISIBLE
        answers?.visibility = View.INVISIBLE
        main.postDelayed({ shoot(key) }, 150)
    }

    private fun showAgain() {
        dot?.visibility = View.VISIBLE
        render()
    }

    private fun shoot(key: String) {
        takeScreenshot(Display.DEFAULT_DISPLAY, mainExecutor, object : TakeScreenshotCallback {
            override fun onSuccess(result: ScreenshotResult) {
                val buffer = result.hardwareBuffer
                val bitmap = try {
                    Bitmap.wrapHardwareBuffer(buffer, result.colorSpace)?.copy(Bitmap.Config.ARGB_8888, false)
                } finally {
                    buffer.close()
                }
                if (bitmap == null) {
                    fail("Capture d'écran impossible.")
                    return
                }
                list.pending = true
                list.message = null
                showAgain()
                worker.execute { process(key, bitmap) }
            }

            override fun onFailure(errorCode: Int) {
                fail(
                    when (errorCode) {
                        ERROR_TAKE_SCREENSHOT_SECURE_WINDOW -> "Cette appli interdit les captures d'écran."
                        ERROR_TAKE_SCREENSHOT_INTERVAL_TIME_SHORT -> "Attends une seconde entre deux captures."
                        else -> "Capture d'écran impossible (code $errorCode)."
                    },
                )
            }
        })
    }

    private fun process(key: String, bitmap: Bitmap) {
        try {
            val png = encode(bitmap)
            val screen = clientFor(key).correct(png, session.previousCut())
            val result = session.record(screen)
            main.post {
                busy = false
                list.add(result)
                showAgain()
            }
        } catch (e: CorrectionError) {
            Log.w(TAG, "${e.message} | ${e.detail}")
            main.post { fail(e.message ?: "Erreur") }
        } catch (e: Exception) {
            Log.e(TAG, "Erreur inattendue", e)
            main.post { fail("Erreur inattendue : réessaie.") }
        }
    }

    private fun fail(message: String) {
        busy = false
        list.error(message)
        showAgain()
    }

    private fun encode(bitmap: Bitmap): String {
        val (width, height) = targetSize(bitmap.width, bitmap.height)
        val sent = if (width != bitmap.width || height != bitmap.height) Bitmap.createScaledBitmap(bitmap, width, height, true) else bitmap
        val out = ByteArrayOutputStream()
        sent.compress(Bitmap.CompressFormat.PNG, 100, out)
        return Base64.encodeToString(out.toByteArray(), Base64.NO_WRAP)
    }

    @Synchronized
    private fun clientFor(key: String): ClaudeClient {
        val baseUrl = prefs.apiBaseUrl.ifBlank { null }
        val id = listOf(key, prefs.modele.id, baseUrl ?: "").joinToString("|")
        client?.let { (cachedId, cached) ->
            if (cachedId == id) return cached
            cached.close()
        }
        return ClaudeClient(key, prefs.modele, baseUrl = baseUrl).also { client = id to it }
    }

    /** Texte affiché, ligne par ligne (pour les tests). */
    fun visibleLines(): List<String> {
        val lines = list.answerLines().map { (numero, texte) -> "$numero  $texte" }.toMutableList()
        if (list.pending) lines += "…"
        list.message?.let { lines += it }
        return if (answers?.visibility == View.VISIBLE) lines else emptyList()
    }

    /** Le point flottant est-il à l'écran ? (pour les tests) */
    fun dotShown(): Boolean = dot?.isAttachedToWindow == true && dot?.visibility == View.VISIBLE

    companion object {
        private const val TAG = "Correcteur"

        @Volatile
        var instance: CorrecteurService? = null
            private set
    }
}
