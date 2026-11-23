package fr.correcteur.annales

import android.app.Activity
import android.content.ComponentName
import android.content.Intent
import android.graphics.Typeface
import android.net.Uri
import android.os.Bundle
import android.provider.Settings
import android.text.InputType
import android.view.Gravity
import android.view.View
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.RadioButton
import android.widget.RadioGroup
import android.widget.ScrollView
import android.widget.TextView
import fr.correcteur.annales.core.ClaudeClient
import fr.correcteur.annales.core.CorrectionError
import fr.correcteur.annales.core.Modele
import java.util.concurrent.Executors

/** Écran de réglages : clé API, activation du service, taille et coin de l'affichage, modèle. */
class MainActivity : Activity() {
    private lateinit var prefs: Prefs
    private lateinit var keyStatus: TextView
    private lateinit var serviceStatus: TextView
    private val background = Executors.newSingleThreadExecutor()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        prefs = Prefs(this)
        val pad = (16 * resources.displayMetrics.density).toInt()
        val column = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(pad * 2, pad * 2, pad * 2, pad * 2)
        }
        setContentView(ScrollView(this).apply { addView(column) })

        column.addView(title("Correcteur d'annales", 26f))
        column.addView(text("Touche le petit point flottant : l'écran est capturé, Claude corrige, et les lettres à cocher s'affichent en tout petit dans un coin."))

        // 1. Clé API
        column.addView(title("1. Ta clé API Anthropic"))
        column.addView(text("Crée-la sur console.anthropic.com (il faut y ajouter un peu de crédit), puis colle-la ici. Elle reste sur la tablette."))
        val keyField = EditText(this).apply {
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD or InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS
            hint = "sk-ant-..."
            setText(prefs.apiKey)
        }
        column.addView(keyField)
        keyStatus = text("")
        column.addView(row(
            button("Enregistrer et tester") {
                prefs.apiKey = keyField.text.toString()
                testKey()
            },
        ))
        column.addView(keyStatus)

        // 2. Service
        column.addView(title("2. Activer le correcteur"))
        serviceStatus = text("")
        column.addView(serviceStatus)
        column.addView(text("Dans Accessibilité, ouvre « Applications installées », choisis Correcteur d'annales et active-le."))
        column.addView(text("Si Android affiche « Paramètre restreint » : ouvre les infos de l'appli (bouton ci-dessous), touche ⋮ en haut à droite, « Autoriser les paramètres restreints », puis recommence."))
        column.addView(row(
            button("Réglages d'accessibilité") { startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS)) },
            button("Infos de l'appli") {
                startActivity(Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.parse("package:$packageName")))
            },
        ))

        // 3. Réglages
        column.addView(title("Taille du texte"))
        column.addView(choices(Prefs.TAILLES.associate { it.toString() to (if (it == 4) "4 (la plus discrète)" else "$it") }, prefs.taille.toString()) {
            prefs.taille = it.toInt()
            CorrecteurService.instance?.refreshSettings()
        })
        column.addView(title("Coin des réponses"))
        column.addView(choices(Prefs.COINS, prefs.coin) {
            prefs.coin = it
            CorrecteurService.instance?.refreshSettings()
        })
        column.addView(title("Modèle"))
        column.addView(choices(Modele.entries.associate { it.id to it.libelle }, prefs.modele.id) {
            prefs.modele = Modele.from(it)
        })

        column.addView(title("Utilisation"))
        column.addView(text("• Touche le point pour corriger l'écran ; fais-le glisser pour le déplacer.\n• Pendant le calcul, « … » s'affiche.\n• « 4  ACD » : à la question 4, coche A, C et D. Les lettres sont toujours celles à cocher, même quand la consigne demande les propositions fausses.\n• Question trop longue pour l'écran : capture le haut, fais défiler, recapture : la réponse se complète.\n• La petite croix ferme l'affichage ; la capture suivante le rouvre.\n• Chaque capture est envoyée à Claude (Anthropic) et facturée sur ton compte API."))
    }

    override fun onResume() {
        super.onResume()
        val enabled = isServiceEnabled()
        serviceStatus.text = if (enabled) "Activé : le petit point est à l'écran." else "Pas encore activé."
        serviceStatus.setTypeface(null, Typeface.BOLD)
        if (prefs.apiKey.isBlank()) keyStatus.text = "Aucune clé enregistrée."
    }

    override fun onDestroy() {
        background.shutdownNow()
        super.onDestroy()
    }

    private fun isServiceEnabled(): Boolean {
        val enabled = Settings.Secure.getString(contentResolver, Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES) ?: return false
        val me = ComponentName(this, CorrecteurService::class.java)
        return enabled.split(':').any { ComponentName.unflattenFromString(it) == me }
    }

    private fun testKey() {
        val key = prefs.apiKey
        if (key.isBlank()) {
            keyStatus.text = "Colle d'abord ta clé."
            return
        }
        keyStatus.text = "Vérification…"
        val modele = prefs.modele
        background.execute {
            val message = try {
                ClaudeClient(key, modele).let { client -> client.check().also { client.close() } }
            } catch (e: CorrectionError) {
                e.message ?: "Erreur"
            }
            runOnUiThread { keyStatus.text = message }
        }
    }

    // -- petits éléments d'interface ------------------------------------------
    private fun title(value: String, size: Float = 19f) = TextView(this).apply {
        text = value
        textSize = size
        setTypeface(null, Typeface.BOLD)
        setPadding(0, (18 * resources.displayMetrics.density).toInt(), 0, (6 * resources.displayMetrics.density).toInt())
    }

    private fun text(value: String) = TextView(this).apply {
        text = value
        textSize = 15f
        setPadding(0, 4, 0, 4)
    }

    private fun button(label: String, action: () -> Unit) = Button(this).apply {
        text = label
        isAllCaps = false
        setOnClickListener { action() }
    }

    private fun row(vararg views: View) = LinearLayout(this).apply {
        orientation = LinearLayout.HORIZONTAL
        gravity = Gravity.START
        views.forEach { addView(it) }
    }

    private fun choices(options: Map<String, String>, selected: String, onChoose: (String) -> Unit) = RadioGroup(this).apply {
        orientation = RadioGroup.VERTICAL
        for ((value, label) in options) {
            val radio = RadioButton(this@MainActivity).apply {
                text = label
                id = View.generateViewId()
                tag = value
                isChecked = value == selected
            }
            addView(radio)
        }
        setOnCheckedChangeListener { group, checkedId -> (group.findViewById<View>(checkedId)?.tag as? String)?.let(onChoose) }
    }
}
