# Robot foot ⚽

Publie automatiquement sur votre page Facebook, sans intervention :

- **le direct** de vos compétitions : buts, penaltys ratés, buts annulés par la VAR, cartons rouges, scores finaux ;
- **les actualités** : transferts, blessures, déclarations, scandales, sélectionnées et réécrites en français par Gemini (gratuit), toujours avec la source.

Chaque publication est accompagnée d'un visuel aux couleurs de votre page.

Le robot tourne gratuitement sur GitHub, toutes les 5 minutes.

## Fichiers

| Fichier | Rôle |
|---|---|
| `config.yaml` | **Vos réglages** : nom de la page, couleurs, compétitions, sources, limites |
| `.github/workflows/robot.yml` | Le « réveil » qui lance le robot toutes les 5 minutes |
| `robot/` | Le programme |
| `state.json` | La mémoire du robot (ne pas modifier) |
| `fonts/` | Polices des visuels (Anton et Montserrat, licence libre OFL) |
| `PRIVACY.md` | Politique de confidentialité exigée par Facebook |

## Clés à ajouter dans GitHub

*Settings → Secrets and variables → Actions → New repository secret*

| Nom | Contenu |
|---|---|
| `API_FOOTBALL_KEY` | Clé API-Football |
| `GEMINI_API_KEY` | Clé Gemini (Google AI Studio) |
| `FB_PAGE_ID` | Identifiant de votre page Facebook |
| `FB_PAGE_TOKEN` | Jeton d'accès permanent de la page |

## Passer du mode test au mode réel

Par défaut, le robot est en **mode test** : il ne publie rien et enregistre les visuels dans l'onglet *Actions* (« apercus-visuels »).

Pour publier pour de vrai : *Settings → Secrets and variables → Actions → Variables → New repository variable*, nom `DRY_RUN`, valeur `false`.

Pour mettre le robot en pause : *Actions → Robot foot → ⋯ → Disable workflow*.

Le guide PDF fourni détaille chaque étape.
