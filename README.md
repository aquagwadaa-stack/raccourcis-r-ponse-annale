# Correcteur d'annales

Un raccourci clavier pour corriger tes annales de médecine sans quitter la page : tu appuies sur **!**, l'écran est capturé, Claude lit les questions, cherche dans **tes cours**, et la réponse s'affiche en tout petit dans le coin de l'écran (`4  ACD`), ou par des coches vertes sur les bonnes propositions.

C'est un outil d'entraînement : il sert à vérifier tes réponses après avoir fait tes questions.

## Installation (Windows)

1. **Télécharge le projet** et décompresse-le où tu veux, par exemple `Documents\correcteur`.
2. **Installe Python** (3.11 ou plus récent) depuis <https://www.python.org/downloads/> si tu ne l'as pas. Pendant l'installation, coche **« Add python.exe to PATH »**.
3. Double-clique sur **`installer.bat`**. Il installe les composants et crée le fichier `config.toml` et le dossier `cours`.
4. Double-clique sur **`connecter_claude.bat`** pour relier ton abonnement Claude (détail ci-dessous).
5. **Mets tes cours** dans le dossier `cours`, puis double-clique sur **`indexer_cours.bat`**.
6. Double-clique sur **`diagnostic.bat`** : chaque ligne doit commencer par `[OK]`.
7. Double-clique sur **`lancer.bat`**. Une icône verte apparaît près de l'horloge : le correcteur est prêt.

**Mettre à jour plus tard** : double-clique sur **`mettre_a_jour.bat`**. Il télécharge la dernière version et garde tes cours, ton historique et tes réglages.

## Connexion à Claude : deux possibilités

**A. Ton abonnement Claude (Pro ou Max), sans frais supplémentaires.** Le correcteur passe par Claude Code, le programme de Claude pour l'ordinateur. Le plan gratuit ne suffit pas.

1. Double-clique sur **`connecter_claude.bat`**. Une fenêtre noire s'ouvre. Si Windows affiche un avertissement de sécurité, choisis « Exécuter ».
2. Si Claude Code n'est pas encore installé, la fenêtre l'installe : pendant une à deux minutes, rien ne bouge. C'est normal, ne la ferme pas.
3. La fenêtre explique ensuite la connexion et te demande d'appuyer sur une touche. Ton navigateur s'ouvre sur claude.ai : connecte-toi si on te le demande, puis clique sur le bouton qui autorise Claude Code.
4. Reviens sur la fenêtre noire : elle affiche **« Connexion reussie »**. Appuie sur une touche pour la fermer.

Si la page du navigateur affiche un code au lieu de se fermer : copie-le, clique dans la fenêtre noire, colle-le avec un **clic droit**, puis appuie sur **Entrée**.

À faire une seule fois. `diagnostic.bat` doit ensuite afficher `[OK]  Connexion à Claude : abonnement Claude connecté`.

<details>
<summary>La même chose à la main, dans PowerShell</summary>

1. Appuie sur **Windows + X**, puis choisis **Windows PowerShell** (ou **Terminal**).
2. Colle cette ligne (clic droit ou Ctrl + V), puis appuie sur Entrée, et attends le message « Claude Code successfully installed! » :
   ```
   irm https://claude.ai/install.ps1 | iex
   ```
3. Ferme la fenêtre, rouvre PowerShell, tape `claude auth login` puis Entrée, et connecte-toi dans le navigateur.

Si PowerShell répond que `claude` n'est pas reconnu, tape à la place : `& "$env:USERPROFILE\.local\bin\claude.exe" auth login`
</details>

Chaque capture consomme une part du quota de ton abonnement (deux appels à Claude, avec une image). Si tu atteins ta limite, le panneau te le dit.

**B. Une clé API Anthropic, facturée à l'usage.** Crée une clé sur <https://console.anthropic.com/> (il faut y ajouter du crédit), puis colle-la dans un fichier texte nommé **`cle_api.txt`**, dans le dossier du correcteur. Ce fichier n'est jamais envoyé sur GitHub.

S'il trouve une clé API, le correcteur l'utilise ; sinon il utilise Claude Code. Pour forcer l'un ou l'autre : `backend = "api"` ou `backend = "claude_cli"` dans `config.toml`.

## Utilisation

| Touche | Action |
|---|---|
| **!** | capture l'écran où se trouve la souris et lance la correction |
| **F9** | masque ou réaffiche les réponses |
| **Maj + F8** | efface les réponses (quand tu changes d'annale) |

Tant que le correcteur tourne, la touche **!** ne sert qu'à lui : pour taper un « ! » ailleurs, clic droit sur l'icône verte, **Mettre les raccourcis en pause** (et pareil pour les réactiver). Les touches se changent dans `config.toml`.

Le menu de l'icône verte (clic droit) propose aussi le choix de l'affichage, la **taille du texte**, et la mise à jour de l'index des cours. Ces choix sont gardés pour la fois suivante.

**Une capture = un écran.** Pendant que Claude travaille, un petit `…` s'affiche : relis tes questions sans faire défiler. Ensuite, fais défiler et rappuie sur **!**.

**Questions trop longues pour l'écran.** Capture ce que tu vois : Claude corrige les propositions visibles. Fais défiler et rappuie sur **!** : il reconnaît la suite de la question, la corrige avec l'énoncé vu sur l'écran d'avant, et complète la réponse (`5  AC` devient `5  ACE`). Une proposition coupée en deux par le bord de l'écran est corrigée à la capture suivante.

### L'affichage (`mode` dans `config.toml`)

- `"lettres"` (par défaut) : en bas à droite, le numéro de la question et les lettres à cocher, en petit, sans fond. Deux réponses au plus : les plus anciennes disparaissent (sauf si l'écran que tu viens de capturer en contient plus, pour ne jamais cacher une réponse toute fraîche). Le texte est blanc avec un contour noir, opaque, comme des sous-titres : il se lit sur une page blanche, colorée ou noire. La petite croix à droite ferme l'affichage ; la capture suivante le rouvre. Tu peux le déplacer en le faisant glisser.
- `"coches"` : une coche verte sur chaque proposition à cocher, directement sur la page, et une croix rouge sur une case que tu as cochée à tort. Les clics traversent les coches. Si tu as fait défiler la page entre-temps, elles tomberaient à côté : le correcteur le détecte et affiche les lettres à la place.
- `"les_deux"`.

Réglages utiles dans `config.toml` : `taille_police` (4 par défaut ; 5, 6, 8, 11 ou 14 aussi dans le menu de l'icône), `reponses_max` (2), `disposition = "ligne"` pour tout mettre sur une seule ligne, `position` pour changer de coin.

### Lire l'affichage

| Ce que tu vois | Ce que ça veut dire |
|---|---|
| `4  ACD` | à la question 4, il faut cocher A, C et D |
| `4  aucune` | aucune proposition n'est à cocher |
| `4  ?` | Claude n'a pas pu corriger cette question : recapture l'écran |
| `…` | la correction est en cours |

**Les lettres affichées sont toujours celles à cocher.** Quand la consigne demande les propositions fausses (« cochez la ou les proposition(s) fausse(s) », « laquelle est inexacte ? »), ce sont donc les fausses qui s'affichent. Les questions à réponse unique (QCU, QRU, « une seule réponse », « la réponse exacte ») donnent une seule lettre.

L'historique (`historique/AAAA-MM-JJ.txt`) donne plus de détails pour chaque question : source de la réponse (ton cours, une annale corrigée, ou les connaissances générales), confiance, consigne inversée, et les cases que tu avais cochées si elles diffèrent.

## Tes cours

Mets dans le dossier `cours` tout ce que tu as : PDF, PowerPoint (`.pptx`), Word (`.docx`), fichiers texte, sous-dossiers compris. **Ajoute aussi tes annales déjà corrigées** : si une question retombe, la correction officielle est reprise telle quelle.

Relance `indexer_cours.bat` chaque fois que tu ajoutes des fichiers (seuls les fichiers nouveaux ou modifiés sont relus).

Pourquoi c'est important : les enseignants notent d'après leur cours, qui ne dit pas toujours la même chose qu'un manuel. Avec le cours indexé, le correcteur suit le cours ; sans lui, il répond d'après les connaissances médicales générales et le signale par `~`.

## Délais et coût

Mesuré pendant le développement, avec l'abonnement Claude (Claude Code) : **12 à 20 secondes par écran** (lecture de l'écran 7 à 14 s, correction 5 à 11 s). Ce sont des mesures sur quelques essais, pas une garantie : cela dépend de la charge du service et de la longueur des questions.

Avec une clé API, rien n'a pu être mesuré (aucune clé n'était disponible pendant le développement). D'après les tarifs publics, il faut compter de l'ordre de 0,15 à 0,25 € par capture avec les réglages par défaut, soit quelques euros pour une annale entière. Deux réglages de `config.toml` réduisent la facture : `modele_lecture = "claude-haiku-5-5"` et `effort_reponse = "medium"`.

## Confidentialité

- Sont envoyés à Anthropic, à chaque capture : l'image de ton écran et les passages de cours retrouvés pour ces questions.
- Tes cours, l'index, l'historique, `config.toml` et `cle_api.txt` restent sur ton PC et sont exclus de GitHub.
- L'historique (`historique/AAAA-MM-JJ.txt`) garde la trace de chaque correction. Il se désactive avec `actif = false`.

## Limites à connaître

- **Les réponses peuvent être fausses**, surtout sans cours sur le sujet. En cas de désaccord, ton cours a raison.
- Les pages scannées (images sans texte) ne sont pas lisibles par l'index : l'indexation te dit lesquelles.
- Les schémas et images des cours ne sont pas pris en compte, seulement le texte.
- Une question à cheval sur deux écrans n'est complétée que si tu captures les deux écrans à la suite (dans les 20 minutes).
- Testé automatiquement sur une machine Windows (installation, mise à jour, touche !, transparence, petite croix), avec un écran à 100 % et un clavier QWERTY ; la position de la touche ! sur un clavier AZERTY est vérifiée à part. Restent à confirmer sur ton PC : l'alignement des coches avec un affichage à 150 ou 200 %.

## Tablette Android

Une appli à part fait la même chose sur une tablette Android (11 ou plus, testée pour la Galaxy Tab S10 FE+) : un **petit point flottant** à toucher, et les lettres à cocher en tout petit dans un coin, dans le même style que sur le PC. Elle appelle Claude directement avec **ta clé API Anthropic** : elle marche partout, sans le PC, mais chaque capture est facturée sur ton compte API, et elle n'utilise pas tes cours.

### Installation (une seule fois)

1. **Crée une clé API** sur <https://console.anthropic.com> depuis la tablette : connecte-toi, ajoute un peu de crédit (Billing), puis « API Keys », « Create Key », et copie la clé (elle commence par `sk-ant-`).
2. **Télécharge l'appli** sur la tablette avec ce lien : <https://github.com/aquagwadaa-stack/raccourcis-r-ponse-annale/releases/download/tablette/correcteur-tablette.apk>. Si le navigateur prévient que le fichier peut être dangereux, choisis « Télécharger quand même ».
3. **Installe-la** en ouvrant le fichier téléchargé. La tablette demande d'autoriser le navigateur à installer des applis : accepte. Si Play Protect affiche un avertissement, touche « Plus de détails » puis « Installer quand même ». Si l'installation est bloquée par le **Bloqueur automatique** de Samsung : Paramètres, Sécurité et confidentialité, Bloqueur automatique, désactive-le le temps de l'installation.
4. **Ouvre l'appli « Correcteur »**, colle ta clé, touche « Enregistrer et tester » : elle doit répondre « Clé valide ».
5. **Active le correcteur** : touche « Réglages d'accessibilité », puis « Applications installées », « Correcteur d'annales », et active-le. Si Android affiche **« Paramètre restreint »** : reviens dans l'appli, touche « Infos de l'appli », puis ⋮ en haut à droite, « Autoriser les paramètres restreints », et recommence.
6. Le petit point apparaît à l'écran. Ouvre ton annale et touche-le.

### Utilisation

- Touche le point pour corriger l'écran ; fais-le glisser pour le déplacer. Il devient gris pendant le calcul, et `…` s'affiche.
- Mêmes règles que sur le PC : `4  ACD` (lettres toujours à cocher, même pour « cochez les fausses »), deux réponses au plus, une petite croix pour fermer, et les questions trop longues se complètent d'une capture à la suivante.
- Dans l'appli : taille du texte (4 par défaut), coin de l'écran, et modèle de Claude.

**Coût (estimation d'après les tarifs publics, pas une mesure)** : avec Opus 5.5, de l'ordre de 0,05 € par capture, soit environ 1 à 2 € pour une annale entière ; avec Sonnet 5.5, environ deux fois moins ; avec Haiku 5.5, presque rien, mais il se trompe plus souvent.

**Confidentialité** : la capture de l'écran est envoyée à Anthropic avec ta clé. L'appli ne lit rien d'autre à l'écran et ne garde ni historique ni capture ; la clé reste dans le stockage privé de l'appli, exclu des sauvegardes.

## En cas de problème

- **Rien ne se passe sur !** : vérifie que les raccourcis ne sont pas en pause (clic droit sur l'icône verte), puis lance `diagnostic.bat`. Si la touche est déjà prise par un autre programme, le correcteur le signale au démarrage ; change-la dans `config.toml`.
- **« Claude Code n'est pas installé »** ou **« pas connecté »** : double-clique sur `connecter_claude.bat`, puis relance `lancer.bat`.
- **« Limite d'utilisation atteinte »** : ton quota Claude est épuisé pour le moment ; réessaie après l'heure indiquée.
- **Les coches sont décalées** : vérifie que tu n'as pas fait défiler la page ; sinon passe en `mode = "lettres"`.
- Le détail technique des erreurs est dans `historique\correcteur.log`.

## Pour aller plus loin

Dans un terminal ouvert dans le dossier du correcteur :

```
.venv\Scripts\python -m correcteur diagnostic
.venv\Scripts\python -m correcteur chercher "protéine Gs adénylate cyclase"
.venv\Scripts\python -m correcteur tester capture1.png capture2.png
```

`tester` corrige des images enregistrées, comme des captures successives, et affiche les réponses dans le terminal ; `chercher` montre les passages de cours retrouvés pour une requête.
