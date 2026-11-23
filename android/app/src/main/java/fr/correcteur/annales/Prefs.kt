package fr.correcteur.annales

import android.content.Context
import fr.correcteur.annales.core.Modele

/** Réglages de l'appli, gardés sur la tablette uniquement (stockage privé, exclu des sauvegardes). */
class Prefs(context: Context) {
    private val p = context.getSharedPreferences("correcteur", Context.MODE_PRIVATE)

    var apiKey: String
        get() = p.getString("cle_api", "") ?: ""
        set(value) = p.edit().putString("cle_api", value.trim()).apply()

    var taille: Int
        get() = p.getInt("taille", 4)
        set(value) = p.edit().putInt("taille", value).apply()

    /** « bas_droite », « bas_gauche », « haut_droite » ou « haut_gauche » */
    var coin: String
        get() = p.getString("coin", "bas_droite") ?: "bas_droite"
        set(value) = p.edit().putString("coin", value).apply()

    var modele: Modele
        get() = Modele.from(p.getString("modele", null))
        set(value) = p.edit().putString("modele", value.id).apply()

    /** Adresse de l'API : vide en usage normal ; les tests la pointent vers un faux serveur. */
    var apiBaseUrl: String
        get() = p.getString("url_api", "") ?: ""
        set(value) = p.edit().putString("url_api", value).apply()

    /** Position du petit point (-1 : pas encore placée). */
    var pointX: Int
        get() = p.getInt("point_x", -1)
        set(value) = p.edit().putInt("point_x", value).apply()

    var pointY: Int
        get() = p.getInt("point_y", -1)
        set(value) = p.edit().putInt("point_y", value).apply()

    companion object {
        val TAILLES = listOf(4, 5, 6, 8, 11, 14)
        val COINS = linkedMapOf(
            "bas_droite" to "En bas à droite",
            "bas_gauche" to "En bas à gauche",
            "haut_droite" to "En haut à droite",
            "haut_gauche" to "En haut à gauche",
        )
    }
}
