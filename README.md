# bot_discord_alternance

Bot Discord auto-hébergé sur VPS qui scrape les offres d'**alternance cybersécurité en Île-de-France** toutes les 2h depuis plusieurs sources, te ping dans un salon dédié et te permet de gérer chaque offre via boutons (`À faire` / `Envoyée` / `Refus` / `Ignorer`).

## Fonctionnalités

- **Sources** : France Travail (API officielle), HelloWork, Welcome to the Jungle, APEC
- **Filtres** : mots-clés cyber (cybersécurité, pentest, SOC, DevSecOps, IAM, etc.) + IDF (75/77/78/91/92/93/94/95) + alternance/apprentissage
- **Dédup** persistant via SQLite — une offre n'est jamais postée deux fois
- **Boutons Discord persistants** : statut modifiable après redémarrage du bot
- **Slash commands** : `/stats`, `/todo`, `/new`, `/search`, `/scrape`
- **Objectif quotidien** : suivi `/stats` affiche progression vers 20 candidatures envoyées/jour
- **Sécurité** : aucun `.env` sur disque, secrets injectés au runtime par GitHub Actions dans `/etc/alternance-bot/env` (mode 640, root:alternance), service systemd hardened (ProtectSystem, NoNewPrivileges, etc.)

## Architecture

```
src/
  config.py            # Lecture stricte env vars (pas de .env)
  db.py                # SQLite async, schéma offers + états
  filters.py           # is_cyber / is_idf / is_alternance + fingerprint
  notifier.py          # Embeds Discord + OfferView (boutons persistants)
  bot.py               # Client discord.py + slash commands
  scheduler.py         # Cycle de scrape périodique
  main.py              # Wiring
  sources/
    base.py            # Source ABC + Offer dataclass
    france_travail.py  # API OAuth2 officielle
    hellowork.py       # Scraping HTML (selectolax)
    wttj.py            # Algolia search (clés publiques)
    apec.py            # API JSON publique
deploy/
  alternance-bot.service  # systemd unit hardened
  install.sh              # Bootstrap VPS (root)
.github/workflows/
  deploy.yml              # Sync code + secrets + restart au push main
```

## Setup VPS OVH (une seule fois)

Sur ton VPS Debian/Ubuntu en root :

```bash
git clone https://github.com/StandFast1/bot_discord_alternance.git /tmp/alt
sudo bash /tmp/alt/deploy/install.sh
```

Le script crée :
- utilisateur système `alternance` (sans shell)
- code à `/opt/alternance-bot`
- venv à `/opt/alternance-bot/.venv`
- data SQLite à `/var/lib/alternance-bot/`
- env file à `/etc/alternance-bot/env` (vide, rempli par GH Actions)
- service systemd `alternance-bot` (activé mais pas démarré)

### Donne `sudo` sans mot de passe au user de déploiement

Le workflow GH Actions se connecte en SSH avec un user (pas root) et utilise `sudo -n`. Crée une règle :

```bash
sudo visudo -f /etc/sudoers.d/alternance-deploy
```
```
<ton-user-deploy> ALL=(ALL) NOPASSWD: /bin/systemctl, /usr/bin/install, /usr/bin/git, /opt/alternance-bot/.venv/bin/pip
```

## Configuration des secrets GitHub

Dans **Settings → Secrets and variables → Actions → New repository secret**, crée :

| Secret | Comment l'obtenir |
|---|---|
| `VPS_HOST` | IP ou hostname de ton VPS OVH |
| `VPS_USER` | User SSH (pas root) |
| `VPS_SSH_KEY` | Clé privée SSH (générée avec `ssh-keygen -t ed25519`, partie privée complète y compris `-----BEGIN/END-----`) |
| `DISCORD_TOKEN` | https://discord.com/developers → ton app → Bot → Reset Token |
| `DISCORD_GUILD_ID` | ID de ton serveur (clic droit sur l'icône serveur en mode dev) |
| `DISCORD_CHANNEL_ID` | ID du salon où poster les offres |
| `DISCORD_USER_ID` | Ton ID Discord (à ping) |
| `FRANCE_TRAVAIL_CLIENT_ID` | https://francetravail.io → crée une app → souscris à *Offres d'emploi v2* |
| `FRANCE_TRAVAIL_CLIENT_SECRET` | idem ci-dessus |

> **Sans France Travail** : la source est skip silencieusement, les 3 autres tournent quand même. Mais France Travail est de loin la plus complète, fais-le.

## Setup application Discord

1. https://discord.com/developers/applications → **New Application**
2. Onglet **Bot** → **Reset Token** → copie dans `DISCORD_TOKEN`
3. **Privileged Gateway Intents** : tout peut rester **off** (le bot n'a besoin que des intents par défaut)
4. **OAuth2 → URL Generator** :
   - Scopes : `bot`, `applications.commands`
   - Bot permissions : `Send Messages`, `Embed Links`, `Read Message History`, `Use Application Commands`
5. Ouvre l'URL générée → ajoute le bot sur ton serveur
6. Active le **Mode développeur** dans Discord (Paramètres → Avancés) pour pouvoir copier les IDs

## Premier déploiement

```bash
git add -A
git commit -m "initial bot"
git push origin main
```

Le workflow `deploy.yml` :
1. SSH au VPS, pull la dernière version
2. Reinstalle les deps si `requirements.txt` a bougé
3. Écrit `/etc/alternance-bot/env` à partir des secrets GH (mode 640, root:alternance — illisible par tout autre user)
4. `systemctl restart alternance-bot`
5. Vérifie que le service est `active`

Suivre les logs :
```bash
sudo journalctl -u alternance-bot -f
```

## Utilisation quotidienne

Dans ton salon Discord :

- Le bot poste chaque nouvelle offre avec **4 boutons** : `📌 À faire` · `✅ Envoyée` · `❌ Refus` · `🗑️ Ignorer`
- `/stats` : tableau de bord + barre de progression vers l'objectif 20 envoyées/jour
- `/todo` : liste des offres marquées "À faire" non encore envoyées
- `/new` : liste des nouvelles non triées
- `/search <mot>` : retrouver une offre dans la base
- `/scrape` : forcer un cycle immédiat (le cycle 2h tourne en parallèle)

## Sécurité — résumé du modèle

- **Aucun `.env`** : `src/config.py` lit uniquement `os.environ`. Si une variable manque, le bot crash au démarrage plutôt que de tourner en silence.
- **Secrets stockés** : GitHub Secrets uniquement. Côté VPS, le seul artéfact en clair est `/etc/alternance-bot/env`, en mode 640 (root écriture, group `alternance` lecture, autres rien).
- **Pas de logs de secrets** : aucun secret loggué par le bot ; le workflow GH Actions n'écrit les valeurs que dans un `EnvironmentFile` envoyé via `scp` chiffré.
- **Service hardened** : `NoNewPrivileges`, `ProtectSystem=strict`, `PrivateTmp`, `RestrictSUIDSGID`, etc. (cf. `deploy/alternance-bot.service`)
- **User dédié** sans shell — le bot ne peut rien faire d'autre que ce qu'il fait.

## Sources école 2600 (Grimp + Bluebox) — auth par cookie

Ces 2 sources sont **derrière login**. La méthode pragmatique : tu copies ton cookie de session depuis ton navigateur, le bot l'utilise. Quand il expire (typiquement quelques semaines), tu rafraîchis le secret GitHub.

### Étape 1 — Récupérer ton cookie de session

1. Connecte-toi normalement sur https://ecole2600.grimp.io (ou bluebox.2600.eu)
2. Ouvre **DevTools** (F12) → onglet **Application** (Chrome/Edge) ou **Storage** (Firefox)
3. Section **Cookies** dans la sidebar → clique sur le domaine `ecole2600.grimp.io`
4. Repère le ou les cookies d'auth — souvent un seul, du genre `_grimp_session`, `session_id`, `connect.sid`, `JWT`, etc.
5. Copie sa **valeur** (clic droit → Copy)
6. Format à mettre dans le secret GitHub : `nom_du_cookie=valeur_du_cookie`
   (s'il y en a plusieurs : `nom1=val1; nom2=val2`)

### Étape 2 — Identifier l'endpoint JSON

Pendant que tu es connecté :

1. DevTools → onglet **Network** → filtre **Fetch/XHR**
2. Recharge la page de liste des offres
3. Cherche une requête qui renvoie un JSON avec les offres (clic dessus → onglet "Response")
4. Note dans l'onglet "Headers" :
   - **Request URL** (l'endpoint exact, ex: `https://ecole2600.grimp.io/api/v1/offers?per_page=50`)
   - **Query string parameters** (les params dans `?...`)
5. Dans **Response** :
   - Si le JSON ressemble à `{"data": [...]}`, note la clé top-level (`data`, `offers`, `results`...)
   - Note les clés de chaque offre (`title`, `company`, `location`, etc.)

### Étape 3 — Ajuster les fichiers source

Dans [src/sources/grimp.py](src/sources/grimp.py) et [src/sources/bluebox.py](src/sources/bluebox.py), modifie en haut du fichier :

```python
API_BASE = "..."              # base de l'URL
LIST_PATH = "/api/..."        # chemin exact de l'endpoint que tu as trouvé
LIST_PARAMS = {               # params à envoyer
    "per_page": 50,
    # ...
}
```

Et si les clés JSON diffèrent de ce que j'ai supposé dans `_to_offer()`, ajuste-les également. Les sources logguent `WARNING ... HTTP XXX` ou `non-JSON response` quand quelque chose foire — `journalctl -u alternance-bot | grep -E "grimp|bluebox"` pour debug.

### Étape 4 — Ajouter les secrets GitHub

Repository secrets supplémentaires :

| Secret | Valeur |
|---|---|
| `GRIMP_COOKIE` | `nom_cookie=valeur_cookie` (depuis ton navigateur sur ecole2600.grimp.io) |
| `BLUEBOX_COOKIE` | idem pour bluebox.2600.eu |

Sans ces secrets, les sources sont skip silencieusement (`return [] if not cookie`).

### Quand le cookie expire

Tu verras dans les logs :
```
WARNING grimp auth expired (HTTP 401) — refresh GRIMP_COOKIE secret
```

Refais l'étape 1, mets à jour le secret GitHub, push n'importe quel commit pour redéployer. Pas de redémarrage manuel nécessaire.

### Légalité

Vérifie le règlement / charte numérique de ton école avant d'utiliser des bots sur leur intranet. Beaucoup l'interdisent même pour des usages personnels. C'est ta responsabilité.

## Fragilité des sources — à savoir

**France Travail** est la seule source 100% officielle/stable : API contractuelle, format JSON garanti. **C'est la source principale.**

Les 3 autres scrapent des endpoints publics non documentés :
- **HelloWork** : sélecteurs HTML (CSS) — peuvent casser à chaque redesign. Si la source renvoie 0 offre alors qu'il y en a sur le site, c'est probablement ça : `src/sources/hellowork.py`, fonction `_parse()`.
- **WTTJ** : Algolia public keys (`ALGOLIA_APP_ID`, `ALGOLIA_API_KEY` dans `src/sources/wttj.py`). Les valeurs codées dans le fichier sont des **placeholders à remplacer** par les vraies clés visibles dans le bundle JS de welcometothejungle.com (cherche `algoliasearch` dans le code source d'une page de recherche). Sans ça, source retourne 0.
- **APEC** : endpoint JSON interne `/cms/webservices/rechercheOffre/list` — stable mais le shape du payload peut bouger ; ajuster `_search()` si la réponse change.

Chaque source échoue **silencieusement** (`safe_fetch` catch l'exception, log un warning) — le bot continue de tourner même si une source casse. Vérifie `journalctl -u alternance-bot | grep "source.*failed"`.

## Pour gratter plus d'offres

20 candidatures/jour c'est très atteignable avec 4 sources + 7 mots-clés × 8 départements (France Travail seul renvoie facilement 100+ offres uniques/semaine sur ce périmètre). Si tu veux pousser :

- Ajoute des mots-clés dans `CYBER_KEYWORDS` (`src/filters.py`) et les listes par source
- Baisse `SCRAPE_INTERVAL_HOURS` à 1 (mais attention rate-limit HelloWork/APEC)
- Ajoute une source : crée `src/sources/<truc>.py` héritant de `Source`, déclare-la dans `src/sources/__init__.py` et `build_sources()` dans `src/main.py`

## Stack

- Python 3.11+
- `discord.py` 2.4 (slash commands, persistent views)
- `httpx` (HTTP async, HTTP/2)
- `selectolax` (parsing HTML rapide)
- SQLite (WAL mode, pas de serveur, parfait pour 1 user / 1 VPS)
