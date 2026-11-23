package fr.correcteur.annales

import android.annotation.SuppressLint
import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.RectF
import android.graphics.Typeface
import android.util.TypedValue
import android.view.MotionEvent
import android.view.View
import android.view.ViewConfiguration
import fr.correcteur.annales.core.AnswerList
import kotlin.math.abs
import kotlin.math.max

/** Couleurs : lettres blanches pleines, contour noir plein, comme des sous-titres (lisible sur tous les fonds). */
private const val ANSWER = Color.WHITE
private val NUMBER = Color.rgb(201, 206, 214)
private val FAINT = Color.rgb(229, 231, 235)
private val ERROR = Color.rgb(255, 120, 120)
private const val OUTLINE = Color.BLACK

/** Taille en « points » des réglages -> pixels, à peu près comme sur le PC. */
fun pointsToPx(context: Context, points: Int): Float =
    TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, points * 4f / 3f, context.resources.displayMetrics)

/** Numéro et lettres à cocher, ex. « 4  ACD », avec une toute petite croix pour fermer. */
@SuppressLint("ViewConstructor")
class AnswerView(context: Context, private val list: AnswerList, private val onClose: () -> Unit) : View(context) {
    private val fill = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.FILL }
    private val stroke = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        color = OUTLINE
        strokeJoin = Paint.Join.ROUND
        strokeCap = Paint.Cap.ROUND
    }
    private val pad: Float get() = max(3f, fill.textSize * 0.35f)
    private val cross = RectF()
    var textSizePx: Float = pointsToPx(context, 4)
        set(value) {
            field = value
            applySize()
            requestLayout()
            invalidate()
        }

    init {
        applySize()
    }

    private fun applySize() {
        // En très petit, un trait trop fin disparaît sous le contour : police un peu plus grasse.
        val typeface = if (textSizePx < pointsToPx(context, 7)) Typeface.create("sans-serif-medium", Typeface.NORMAL) else Typeface.SANS_SERIF
        for (paint in listOf(fill, stroke)) {
            paint.textSize = textSizePx
            paint.typeface = typeface
        }
        stroke.strokeWidth = max(2f, textSizePx * 0.22f)
    }

    /** Lignes à dessiner : (segments (texte, couleur)) */
    private fun lines(): List<List<Pair<String, Int>>> {
        val out = mutableListOf<List<Pair<String, Int>>>()
        for ((numero, texte) in list.answerLines()) out += listOf(numero to NUMBER, "  " to 0, texte to ANSWER)
        if (list.pending) out += listOf(listOf("…" to FAINT))
        list.message?.let { out += listOf(listOf(it to if (list.messageIsError) ERROR else FAINT)) }
        return out
    }

    private fun lineHeight(): Float = fill.fontMetrics.let { it.descent - it.ascent }

    private fun crossSize(): Float = max(6f, lineHeight() * 0.36f)

    override fun onMeasure(widthMeasureSpec: Int, heightMeasureSpec: Int) {
        val all = lines()
        val textWidth = all.maxOfOrNull { line -> line.sumOf { fill.measureText(it.first).toDouble() } }?.toFloat() ?: 0f
        val width = pad + textWidth + crossSize() * 1.6f + pad
        val height = pad + max(all.size, 1) * lineHeight() + pad
        setMeasuredDimension(width.toInt() + 1, height.toInt() + 1)
    }

    override fun onDraw(canvas: Canvas) {
        val metrics = fill.fontMetrics
        var baseline = pad - metrics.ascent
        for (line in lines()) {
            var x = pad
            for ((text, color) in line) {
                if (color != 0) {
                    canvas.drawText(text, x, baseline, stroke)
                    fill.color = color
                    canvas.drawText(text, x, baseline, fill)
                }
                x += fill.measureText(text)
            }
            baseline += lineHeight()
        }
        // Petite croix en haut à droite.
        val size = crossSize()
        val right = width - pad
        val top = pad + (lineHeight() - size) / 2
        cross.set(right - size, top, right, top + size)
        val line = Paint(Paint.ANTI_ALIAS_FLAG).apply { strokeCap = Paint.Cap.ROUND }
        for ((color, widthPx) in listOf(OUTLINE to max(3f, size * 0.45f), NUMBER to max(1.5f, size * 0.18f))) {
            line.color = color
            line.strokeWidth = widthPx
            canvas.drawLine(cross.left, cross.top, cross.right, cross.bottom, line)
            canvas.drawLine(cross.right, cross.top, cross.left, cross.bottom, line)
        }
    }

    @SuppressLint("ClickableViewAccessibility")
    override fun onTouchEvent(event: MotionEvent): Boolean {
        if (event.action == MotionEvent.ACTION_UP) {
            val margin = max(12f, crossSize())
            if (event.x >= cross.left - margin && event.y <= cross.bottom + margin) onClose()
        }
        return true
    }
}

/** Petit point flottant : on le touche pour corriger, on le fait glisser pour le déplacer. */
@SuppressLint("ViewConstructor")
class DotView(
    context: Context,
    private val onTap: () -> Unit,
    private val onMove: (dx: Int, dy: Int) -> Unit,
    private val onMoved: () -> Unit,
) : View(context) {
    private val sizePx = TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, 16f, resources.displayMetrics)
    private val touchSlop = ViewConfiguration.get(context).scaledTouchSlop
    private val ring = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.STROKE; color = OUTLINE }
    private val disc = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.FILL; color = Color.WHITE }
    private var downX = 0f
    private var downY = 0f
    private var lastX = 0f
    private var lastY = 0f
    private var dragging = false
    var busy = false
        set(value) {
            field = value
            invalidate()
        }

    init {
        alpha = 0.55f // discret
        contentDescription = "Corriger l'écran"
    }

    override fun onMeasure(widthMeasureSpec: Int, heightMeasureSpec: Int) {
        // Zone tactile plus large que le point dessiné, pour le toucher facilement.
        val touch = (sizePx * 2.2f).toInt()
        setMeasuredDimension(touch, touch)
    }

    override fun onDraw(canvas: Canvas) {
        val radius = sizePx / 2
        ring.strokeWidth = radius * 0.35f
        disc.color = if (busy) Color.rgb(160, 160, 160) else Color.WHITE
        canvas.drawCircle(width / 2f, height / 2f, radius, disc)
        canvas.drawCircle(width / 2f, height / 2f, radius, ring)
    }

    @SuppressLint("ClickableViewAccessibility")
    override fun onTouchEvent(event: MotionEvent): Boolean {
        when (event.actionMasked) {
            MotionEvent.ACTION_DOWN -> {
                downX = event.rawX; downY = event.rawY; lastX = downX; lastY = downY; dragging = false
            }
            MotionEvent.ACTION_MOVE -> {
                if (!dragging && (abs(event.rawX - downX) > touchSlop || abs(event.rawY - downY) > touchSlop)) dragging = true
                if (dragging) {
                    onMove((event.rawX - lastX).toInt(), (event.rawY - lastY).toInt())
                    lastX = event.rawX; lastY = event.rawY
                }
            }
            MotionEvent.ACTION_UP -> if (dragging) onMoved() else onTap()
        }
        return true
    }
}
