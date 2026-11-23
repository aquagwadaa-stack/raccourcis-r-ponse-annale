package fr.correcteur.annales.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class CorrectionTest {
    private val json = """
        Voici : {"questions": [
          {"numero": "Question 4 :", "debut_visible": true, "complete": true, "type": "QRM", "cocher": "vraies",
           "enonce": "", "propositions": [
             {"lettre": "a.", "texte": "", "vraie": true}, {"lettre": "B", "texte": "", "vraie": false},
             {"lettre": "C", "texte": "", "vraie": true}], "reponse_libre": "", "confiance": "haute"},
          {"numero": "5", "debut_visible": true, "complete": true, "type": "QRM", "cocher": "fausses",
           "enonce": "", "propositions": [
             {"lettre": "A", "texte": "", "vraie": false}, {"lettre": "B", "texte": "", "vraie": true},
             {"lettre": "C", "texte": "", "vraie": true}, {"lettre": "D", "texte": "", "vraie": false}],
           "reponse_libre": "", "confiance": "moyenne"}
        ]}
    """.trimIndent()

    @Test
    fun lecture_et_lettres_a_cocher() {
        val screen = parseScreen(json)
        assertEquals(listOf("4", "5"), screen.questions.map { it.numero })
        assertEquals("AC", screen.questions[0].lettres())
        // « cochez les fausses » : ce sont les fausses qu'il faut cocher
        assertEquals("AD", screen.questions[1].lettres())
    }

    @Test
    fun normalisations() {
        assertEquals("12", normalizeNumber("Question 12 :"))
        assertEquals("7", normalizeNumber("07"))
        assertEquals("", normalizeNumber(""))
        assertEquals("B", normalizeLetter(" b. "))
        assertEquals("AC", lettersToTick(mapOf("C" to true, "A" to true, "B" to false), false))
    }

    @Test(expected = CorrectionError::class)
    fun reponse_illisible() {
        parseScreen("pas de JSON ici")
    }

    @Test
    fun taille_de_l_image_envoyee() {
        assertEquals(1920 to 1080, targetSize(1920, 1080))
        val (w, h) = targetSize(2880, 1800) // écran de la Galaxy Tab S10 FE+
        assertTrue(w <= 2576 && w * h <= 3_750_000)
        assertEquals(1.6, w.toDouble() / h, 0.01)
    }
}

class SessionTest {
    private fun q(
        numero: String,
        lettres: String,
        vraies: String,
        complete: Boolean = true,
        debut: Boolean = true,
        fausses: Boolean = false,
        enonce: String = "",
    ) = Question(
        numero, debut, complete, "QRM", fausses, enonce,
        lettres.map { Proposition(it.toString(), "texte $it", it in vraies) }, "", "haute",
    )

    @Test
    fun question_a_cheval_sur_deux_captures() {
        val session = Session()
        val first = session.record(Screen(listOf(q("4", "AB", "A"), q("5", "ABC", "AC", complete = false, enonce = "Concernant FasL"))))
        assertEquals(listOf("4" to "A", "5" to "AC"), first.map { it.numero to it.lettres })
        val cut = session.previousCut()!!
        assertEquals("5", cut.numero)
        assertEquals("Concernant FasL", cut.enonce)

        // Le haut de la capture suivante : D et E, sans numéro ; la correction ne juge que celles-là.
        val second = session.record(Screen(listOf(q("", "DE", "E", debut = false), q("6", "AB", "B"))))
        assertEquals(listOf("5" to "ACE", "6" to "B"), second.map { it.numero to it.lettres })
        assertNull(session.previousCut())
    }

    @Test
    fun question_sur_trois_captures_garde_son_debut() {
        val session = Session()
        session.record(Screen(listOf(q("7", "AB", "B", complete = false, enonce = "Long cas clinique"))))
        session.record(Screen(listOf(q("", "CD", "C", complete = false, debut = false))))
        val cut = session.previousCut()!!
        assertEquals("7", cut.numero)
        assertEquals("Long cas clinique", cut.enonce)
        assertEquals(listOf("A", "B", "C", "D"), cut.propositions.map { it.lettre })
        val last = session.record(Screen(listOf(q("7", "E", "E", debut = false))))
        assertEquals("BCE", last.single().lettres)
    }

    @Test
    fun question_revue_en_entier_remplace_et_consigne_inversee() {
        val session = Session()
        session.record(Screen(listOf(q("3", "ABF", "AF"))))
        assertEquals("B", session.record(Screen(listOf(q("3", "AB", "B")))).single().lettres)
        assertEquals("A", session.record(Screen(listOf(q("3", "AB", "B", fausses = true)))).single().lettres)
    }

    @Test
    fun memoire_oubliee_apres_un_long_moment() {
        var now = 0L
        val session = Session(maxAgeMs = 1000, clock = { now })
        session.record(Screen(listOf(q("5", "AB", "A", complete = false))))
        now = 2000
        assertNull(session.previousCut())
        assertEquals("E", session.record(Screen(listOf(q("5", "E", "E", debut = false)))).single().lettres)
    }

    @Test
    fun enonce_seul_ignore() {
        val session = Session()
        val answers = session.record(Screen(listOf(q("8", "", "", complete = false, enonce = "Énoncé seul"))))
        assertTrue(answers.isEmpty())
        assertEquals("Énoncé seul", session.previousCut()!!.enonce)
    }
}

class AnswerListTest {
    private fun a(numero: String, lettres: String) = Answer(numero, lettres)

    @Test
    fun deux_reponses_au_plus_sauf_ecran_plus_charge() {
        val list = AnswerList()
        list.add(listOf(a("4", "ACD"), a("5", "C")))
        list.add(listOf(a("6", "BE")))
        assertEquals(listOf("5" to "C", "6" to "BE"), list.answerLines())
        list.add(listOf(a("7", "A"), a("8", "B"), a("9", "C")))
        assertEquals(listOf("7", "8", "9"), list.answerLines().map { it.first })
        list.add(listOf(a("10", "D")))
        assertEquals(listOf("9" to "C", "10" to "D"), list.answerLines())
    }

    @Test
    fun attente_erreur_et_fermeture() {
        val list = AnswerList()
        assertTrue(list.isEmpty())
        list.pending = true
        assertFalse(list.isEmpty())
        list.error("Clé API refusée")
        assertFalse(list.pending)
        assertEquals("Clé API refusée", list.message)
        list.add(emptyList())
        assertEquals("aucune question sur cet écran", list.message)
        list.add(listOf(a("1", "")))
        assertEquals(listOf("1" to "aucune"), list.answerLines())
        list.clear()
        assertTrue(list.isEmpty())
    }
}

class PromptsTest {
    @Test
    fun contexte_de_la_question_coupee() {
        assertEquals("Voici la capture. Corrige les questions visibles.", Prompts.userText(null))
        val previous = Question(
            "5", true, false, "QRM", false, "Concernant FasL :",
            listOf(Proposition("A", "FasL est un récepteur", false)), "", "haute",
        )
        val text = Prompts.userText(previous)
        assertTrue(text.contains("question 5, coupée en bas de l'écran"))
        assertTrue(text.contains("Énoncé : Concernant FasL :"))
        assertTrue(text.contains("A. FasL est un récepteur (écran précédent)"))
        assertTrue(text.contains("donne-lui le numéro 5"))
    }

    @Test
    fun format_impose() {
        @Suppress("UNCHECKED_CAST")
        val item = ((Prompts.SCHEMA["properties"] as Map<String, Any>)["questions"] as Map<String, Any>)["items"] as Map<String, Any>
        @Suppress("UNCHECKED_CAST")
        val properties = item["properties"] as Map<String, Any>
        assertEquals(item["required"], properties.keys.toList())
        assertEquals(false, item["additionalProperties"])
        assertTrue(Prompts.SYSTEM.contains("cocher"))
    }
}
