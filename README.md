# La Caisse d'Épargne Colombienne

Application Flask de gestion de cotisations, membres et dépenses.
Base de données : **Postgres hébergé sur Supabase** (projet `caisse-colombienne`,
région eu-west-1).

## Installation

```bash
python3 -m venv venv
source venv/bin/activate          # sous Windows : venv\Scripts\activate
pip install -r requirements.txt
```

## Configuration

Copiez `.env.example` en `.env` et remplissez :

- `SECRET_KEY` — générez-la avec `python3 -c "import secrets; print(secrets.token_hex(32))"`
- `DATABASE_URL` — la chaîne de connexion Supabase. Je ne peux pas la récupérer
  pour toi (le mot de passe de la base n'est jamais exposé par l'API) :
  va dans le dashboard Supabase du projet **caisse-colombienne** →
  **Project Settings > Database > Connection string**, copie l'URI, et
  remplace `[VOTRE-MOT-DE-PASSE]` par le mot de passe de la base (défini à
  la création du projet, ou réinitialisable depuis cette même page).

```bash
export $(grep -v '^#' .env | xargs)   # Linux/Mac
python3 app.py
```

Si `ADMIN_PASSWORD` n'est pas défini, un mot de passe temporaire est généré
et affiché **une seule fois** dans la console au premier lancement — notez-le
immédiatement.

## Ce qui a été corrigé par rapport à la version initiale

| Problème | Correctif |
|---|---|
| Aucune authentification, accès libre à tout le monde | Authentification par session (Flask-Login) + page `/login` |
| Rôles inexistants : n'importe qui pouvait tout modifier | Deux rôles : `tresorier` (lecture + écriture) et `lecture` (consultation seule) ; les routes de modification sont protégées par `@role_required("tresorier")` |
| Pas de protection CSRF sur les formulaires POST | `Flask-WTF` (`CSRFProtect`) activé globalement, jeton ajouté à chaque formulaire (y compris ceux générés en JavaScript) |
| `secret_key` codée en dur dans le fichier | Lue depuis la variable d'environnement `SECRET_KEY` |
| `debug=True` par défaut | Désactivé par défaut, activable uniquement via `FLASK_DEBUG=1` |
| Aucun compte utilisateur, pas de mots de passe | Table `utilisateurs` avec mots de passe hachés (`werkzeug.security`) |
| SQLite = fichier local, perdu si le disque du serveur est éphémère | Postgres géré par Supabase : disque persistant, sauvegardes automatiques, accessible depuis n'importe quel hébergeur |

## Comptes et rôles

Trois rôles :

- **super_admin** — toi, le propriétaire. Un seul compte, créé automatiquement
  au premier lancement (`ADMIN_USERNAME` / `ADMIN_PASSWORD`). Seul lui gère
  les comptes (page `/utilisateurs`) et attribue le rôle trésorier.
- **tresorier** — un seul compte possible, garanti par une contrainte unique
  au niveau de la base Postgres elle-même (pas seulement dans le code). Peut
  ajouter/modifier/supprimer membres, cotisations, dépenses.
- **lecture** — consultation seule. C'est le rôle de **tout compte qui
  s'inscrit**, sans exception.

**Inscription** : sur `/inscription`, avec prénom, nom, **numéro de
téléphone** (format international, ex: `+2250700000001`), mot de passe, et
le **code d'inscription** que tu donnes (variable `INSCRIPTION_CODE`) — un
mot de passe collectif simple qui évite que n'importe qui tombant sur
l'URL puisse créer un compte. Le compte est actif immédiatement, connexion
automatique après inscription — **aucune vérification du numéro** n'est
faite (pas de SMS, pas d'email). C'est un choix assumé : comme tout compte
inscrit est en lecture seule et que seul toi peux promouvoir quelqu'un
trésorier, le risque d'un faux numéro est faible — au pire, un compte
lecture seule fantôme, sans accès en écriture.

**C'est toi, le super_admin**, qui choisis qui devient trésorier, depuis
`/utilisateurs` (menu déroulant de rôle à côté de chaque compte). Le rôle
trésorier reste garanti unique par la contrainte SQL, donc même une erreur
de manipulation de ta part serait rejetée par la base si un trésorier
existe déjà.

## Base de données (Supabase)

- Projet : `caisse-colombienne` (région `eu-west-1`)
- Tables : `utilisateurs`, `membres`, `cotisations`, `depenses`
- **Row Level Security activée sans policy** sur les 4 tables : l'app se
  connecte en direct via `DATABASE_URL` (le rôle `postgres`, qui contourne
  RLS), donc ça ne bloque rien côté app. Ça bloque en revanche tout accès
  via l'API REST publique de Supabase (clé anon), qu'on n'utilise pas ici —
  c'est voulu, ça ferme une porte inutile.
- `app.py` recrée les tables avec `CREATE TABLE IF NOT EXISTS` au démarrage
  si elles n'existent pas déjà (filet de sécurité), mais elles sont déjà en
  place sur le projet Supabase créé pour toi.

## Déploiement sur Vercel (recommandé, zero-config)

Le projet est prêt tel quel : `app.py` expose une instance Flask nommée
`app`, `pyproject.toml` pointe explicitement dessus (`entrypoint = "app:app"`),
`vercel.json` donne à la fonction 30 secondes de marge, `.python-version`
fixe Python 3.12. Aucun fichier à créer.

**Point obligatoire, pas optionnel** : Vercel n'accepte que l'**IPv4** en
sortie. La connexion directe à Supabase (`db.xxx.supabase.co:5432`) est en
IPv6 et **ne fonctionnera pas du tout** depuis Vercel — pas "moins bien",
elle ne se connectera jamais. Utilise obligatoirement le **Shared Pooler en
mode Transaction** (port 6543, IPv4) — voir `.env.example`, déjà mis à jour
avec le bon format.

### Étapes

1. Pousse le projet sur un dépôt Git (GitHub/GitLab/Bitbucket) — Vercel
   déploie depuis un repo, pas depuis un zip.
2. Sur vercel.com → **Add New → Project** → importe le repo.
3. Avant de déployer, va dans **Environment Variables** et ajoute (jamais
   dans un fichier commité, uniquement ici) :
   - `SECRET_KEY`
   - `DATABASE_URL` — le lien du **Transaction pooler** (dashboard Supabase
     → bouton **Connect** → onglet **Transaction pooler**), pas la connexion
     directe
   - `ADMIN_USERNAME`, `ADMIN_PASSWORD`
   - `INSCRIPTION_CODE`
   - `FLASK_DEBUG` = `0`
4. Déploie. Vercel détecte `app.py` automatiquement.
5. Teste `https://ton-projet.vercel.app/login`.

`wsgi.py` et `Procfile` sont ignorés par Vercel (`.vercelignore`) — ils
restent utiles seulement pour PythonAnywhere/Render/VPS (voir plus bas).

## Autres hébergeurs

### PythonAnywhere — ⚠️ le compte gratuit ne fonctionnera probablement pas

Correction par rapport à ce que je t'ai dit plus tôt : les comptes gratuits
PythonAnywhere limitent les connexions sortantes à une liste blanche de
sites (HTTP/HTTPS), et ne permettent pas de connexions sur un port
arbitraire comme Postgres (5432/6543). Le `wsgi.py` fourni ne fonctionnera
donc que sur un **compte payant** PythonAnywhere (accès réseau illimité).
Sur gratuit, pars sur Vercel ci-dessus.

### Render / Railway / Fly.io

1. Pousse le projet sur un dépôt Git.
2. Connecte le dépôt à la plateforme choisie.
3. Renseigne les mêmes variables d'environnement que pour Vercel — utilise
   aussi le pooler transaction (ces plateformes ont aussi tendance à être
   IPv4-only ou à bénéficier de connexions plus courtes).
4. Déploie — le `Procfile` (`gunicorn app:app --bind 0.0.0.0:$PORT`) est
   déjà reconnu par ces plateformes.

### VPS (si tu veux tout contrôler)

`pip install -r requirements.txt`, variables d'environnement (connexion
directe possible ici, un VPS a l'IPv6), `gunicorn app:app --bind
127.0.0.1:5050`, puis systemd + nginx + Certbot devant. Dis-moi si tu veux
ces fichiers de config.

### À ne pas faire

- Ne mets jamais `FLASK_DEBUG=1` en ligne.
- Change le mot de passe admin généré par défaut dès la première connexion.
- Ne partage jamais `DATABASE_URL` (elle contient le mot de passe de la base) — elle va uniquement dans les variables d'environnement de l'hébergeur, jamais dans le code ni sur Git (`.gitignore`/`.vercelignore` l'excluent déjà via `.env`).
