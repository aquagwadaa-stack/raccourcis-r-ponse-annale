# Correcteur d'annales

Un raccourci clavier pour corriger tes annales de médecine sans quitter la page : tu appuies sur **F8**, l'écran est capturé, Claude lit les questions, cherche dans **tes cours**, et la réponse s'affiche par-dessus l'écran — les lettres seules (`4  ACD`) ou des coches vertes sur les bonnes propositions.

C'est un outil d'entraînement : il sert à vérifier tes réponses après avoir fait tes questions.

## Installation (Windows)

1. **Télécharge le projet** et décompresse-le où tu veux, par exemple `Documents\correcteur`.
2. **Installe Python** (3.11 ou plus récent) depuis <https://www.python.org/downloads/> si tu ne l'as pas. Pendant l'installation, coche **« Add python.exe to PATH »**.
3. Double-clique sur **`installer.bat`**. Il installe les composants et crée le fichier `config.toml` et le dossier `cours`.
4. **Connecte Claude** (voir ci-dessous).
5. **Mets tes cours** dans le dossier `cours`, puis double-clique sur **`indexer_cours.bat`**.
6. Double-clique sur **`diagnostic.bat`** : chaque ligne doit commencer par `[OK]`.
7. Double-clique sur **`lancer.bat`**. Une icône verte apparaît près de l'horloge : le correcteur est prêt.

## Connexion à Claude : deux possibilités

**A. Ton abonnement Claude (Pro ou Max), sans frais supplémentaires.** Le correcteur passe par Claude Code.

1. Ouvre PowerShell (menu Démarrer, tape « PowerShell ») et colle :
   ```
   irm https://claude.ai/install.ps1 | iex
   ```
2. Ferme puis rouvre PowerShell, tape `claude`, et connecte-toi avec ton compte dans le navigateur qui s'ouvre. Tu peux ensuite fermer la fenêtre.

Chaque capture consomme une part du quota de ton abonnement (deux appels à Claude, avec une image). Si tu atteins ta limite, le panneau te le dit.

**B. Une clé API Anthropic, facturée à l'usage.** Crée une clé sur <https://console.anthropic.com/> (il faut y ajouter du crédit), puis colle-la dans un fichier texte nommé **`cle_api.txt`**, dans le dossier du correcteur. Ce fichier n'est jamais envoyé sur GitHub.

S'il trouve une clé API, le correcteur l'utilise ; sinon il utilise Claude Code. Pour forcer l'un ou l'autre : `backend = "api"` ou `backend = "claude_cli"` dans `config.toml`.

## Utilisation

| Touche | Action |
|---|---|
| **F8** | capture l'écran où se trouve la souris et lance la correction |
| **F9** | masque ou réaffiche les réponses |
| **Maj + F8** | efface la liste (quand tu changes d'annale) |

Les touches se changent dans `config.toml`. Le menu de l'icône verte (clic droit) propose les mêmes actions, le choix de l'affichage et la mise à jour de l'index des cours.

**Une capture = un écran.** Pendant que Claude travaille, relis tes questions sans faire défiler. Ensuite, fais défiler et rappuie sur F8. Tu peux lancer plusieurs captures à la suite : les réponses s'ajoutent dans le panneau, classées par numéro de question.

### Les trois affichages (`mode` dans `config.toml`)

- `"lettres"` : un petit panneau avec le numéro de la question et les lettres justes.
- `"coches"` : une coche verte sur chaque bonne proposition, directement sur la page. Les clics traversent les coches.
- `"les_deux"`.

Les coches sont placées d'après la capture. Si tu as fait défiler la page entre-temps, elles tomberaient à côté : le correcteur le détecte et affiche les lettres à la place.

### Lire le panneau

| Ce que tu vois | Ce que ça veut dire |
|---|---|
| `4  ACD` | les propositions A, C et D sont justes |
| `4  aucune` | aucune proposition n'est juste |
| lettres **vertes** | tes cases cochées sont identiques à la correction |
| lettres **orange** + `toi : AC` | tes cases diffèrent ; `toi :` rappelle ce que tu avais coché |
| `~` après les lettres | réponse donnée **sans appui dans tes cours** (connaissances générales) |
| `?` après les lettres | Claude est peu sûr de lui |
| `coupée` | la question dépasse de l'écran : fais défiler et rappuie sur F8 |
| croix rouge sur la page | une case que tu as cochée et qui est fausse |

La comparaison avec tes cases se désactive avec `comparer_mes_reponses = false`.

## Tes cours

Mets dans le dossier `cours` tout ce que tu as : PDF, PowerPoint (`.pptx`), Word (`.docx`), fichiers texte, sous-dossiers compris. **Ajoute aussi tes annales déjà corrigées** : si une question retombe, la correction officielle est reprise telle quelle.

Relance `indexer_cours.bat` chaque fois que tu ajoutes des fichiers (seuls les fichiers nouveaux ou modifiés sont relus).

Pourquoi c'est important : les enseignants notent d'après leur cours, qui ne dit pas toujours la même chose qu'un manuel. Avec le cours indexé, le correcteur suit le cours ; sans lui, il répond d'après les connaissances médicales générales et le signale par `~`.

## Délais et coût

Mesuré pendant le développement, avec l'abonnement Claude (Claude Code), sur un écran de trois questions : **environ 20 secondes** (lecture de l'écran 10 à 14 s, correction 5 à 11 s). Ce sont des mesures sur quelques essais, pas une garantie : cela dépend de la charge du service et de la longueur des questions.

Avec une clé API, rien n'a pu être mesuré (aucune clé n'était disponible pendant le développement). D'après les tarifs publics, il faut compter de l'ordre de 0,15 à 0,25 € par capture avec les réglages par défaut, soit quelques euros pour une annale entière. Deux réglages de `config.toml` réduisent la facture : `modele_lecture = "claude-haiku-5-5"` et `effort_reponse = "medium"`.

## Confidentialité

- Sont envoyés à Anthropic, à chaque capture : l'image de ton écran et les passages de cours retrouvés pour ces questions.
- Tes cours, l'index, l'historique, `config.toml` et `cle_api.txt` restent sur ton PC et sont exclus de GitHub.
- L'historique (`historique/AAAA-MM-JJ.txt`) garde la trace de chaque correction. Il se désactive avec `actif = false`.

## Limites à connaître

- **Les réponses peuvent être fausses**, surtout sans cours sur le sujet. En cas de désaccord, ton cours a raison.
- Les pages scannées (images sans texte) ne sont pas lisibles par l'index : l'indexation te dit lesquelles.
- Les schémas et images des cours ne sont pas pris en compte, seulement le texte.
- Une question n'est corrigée que si elle est entièrement visible à l'écran.
- La partie propre à Windows (raccourcis, lancement) a été écrite et testée automatiquement, mais pas encore essayée sur un vrai PC : dis-moi ce qui coince.

## En cas de problème

- **Rien ne se passe sur F8** : lance `diagnostic.bat`. Si le raccourci est déjà pris par un autre programme, le panneau le signale au démarrage ; change-le dans `config.toml`.
- **« Claude Code est introuvable »** : refais l'étape A ci-dessus, puis relance `lancer.bat`.
- **« Limite d'utilisation atteinte »** : ton quota Claude est épuisé pour le moment ; réessaie après l'heure indiquée.
- **Les coches sont décalées** : vérifie que tu n'as pas fait défiler la page ; sinon passe en `mode = "lettres"`.
- Le détail technique des erreurs est dans `historique\correcteur.log`.

## Pour aller plus loin

Dans un terminal ouvert dans le dossier du correcteur :

```
.venv\Scripts\python -m correcteur diagnostic
.venv\Scripts\python -m correcteur chercher "protéine Gs adénylate cyclase"
.venv\Scripts\python -m correcteur tester capture.png
```

`tester` corrige une image enregistrée et affiche les réponses dans le terminal ; `chercher` montre les passages de cours retrouvés pour une requête.
