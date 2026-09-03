import os
import secrets
from datetime import datetime, timedelta, timezone
from functools import wraps

import psycopg2
import psycopg2.extras
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from flask_login import (
    LoginManager,
    UserMixin,
    login_user,
    logout_user,
    login_required,
    current_user,
)
from flask_wtf import CSRFProtect
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)

# --- Sécurité : clé secrète -------------------------------------------------
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
if not os.environ.get("SECRET_KEY"):
    print(
        "[ATTENTION] SECRET_KEY n'est pas définie dans l'environnement. "
        "Une clé temporaire a été générée pour cette session uniquement. "
        "Définissez SECRET_KEY avant de déployer en production."
    )

DEBUG_MODE = os.environ.get("FLASK_DEBUG", "0") == "1"

# Code d'inscription partagé : les vrais membres doivent le connaître pour
# créer un compte. Sans lui, l'inscription est ouverte à n'importe qui qui
# tombe sur l'URL — donne ce code uniquement aux membres de la caisse.
INSCRIPTION_CODE = os.environ.get("INSCRIPTION_CODE")
if not INSCRIPTION_CODE:
    print(
        "[ATTENTION] INSCRIPTION_CODE n'est pas définie : l'inscription est "
        "ouverte à n'importe qui connaissant l'URL de l'application. "
        "Définissez INSCRIPTION_CODE pour la restreindre aux vrais membres."
    )

# --- Base de données : Postgres (Supabase) ----------------------------------
DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise SystemExit(
        "DATABASE_URL n'est pas définie. Copiez la chaîne de connexion depuis "
        "Supabase (Project Settings > Database > Connection string) dans votre "
        "fichier .env avant de lancer l'application."
    )


class DBConnection:
    """Petit wrapper autour de psycopg2 qui garde l'API utilisée dans le
    reste du code (conn.execute(...).fetchone()/.fetchall(), ? comme
    placeholder) pour limiter la casse par rapport à la version SQLite."""

    def __init__(self, pg_conn):
        self._conn = pg_conn

    def execute(self, query, params=()):
        cur = self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(query.replace("?", "%s"), params)
        return cur

    def executemany(self, query, seq_of_params):
        cur = self._conn.cursor()
        cur.executemany(query.replace("?", "%s"), seq_of_params)
        return cur

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()


def get_db_connection():
    return DBConnection(psycopg2.connect(DATABASE_URL))


# --- Protection CSRF ---------------------------------------------------------
csrf = CSRFProtect(app)

# --- Authentification ---------------------------------------------------------
login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.login_message = "Veuillez vous connecter pour accéder à la caisse."
login_manager.login_message_category = "danger"


class Utilisateur(UserMixin):
    def __init__(self, row):
        self.id = str(row["id"])
        self.username = row["username"]
        self.role = row["role"]
        self.nom = row["nom"] or ""
        self.prenom = row["prenom"] or ""

    @property
    def nom_complet(self):
        complet = f"{self.prenom} {self.nom}".strip()
        return complet or self.username


@login_manager.user_loader
def load_user(user_id):
    conn = get_db_connection()
    row = conn.execute(
        "SELECT * FROM utilisateurs WHERE id = ?", (user_id,)
    ).fetchone()
    conn.close()
    return Utilisateur(row) if row else None


def role_required(*roles):
    """Restreint une route aux utilisateurs ayant un des rôles donnés."""

    def decorator(view_func):
        @wraps(view_func)
        @login_required
        def wrapped(*args, **kwargs):
            if current_user.role not in roles:
                flash("Action non autorisée pour votre rôle.", "danger")
                return redirect(url_for("index"))
            return view_func(*args, **kwargs)

        return wrapped

    return decorator


def init_db():
    conn = get_db_connection()

    # Filet de sécurité : les tables sont normalement déjà créées via une
    # migration Supabase, mais ceci permet aussi de démarrer contre une base
    # Postgres vierge sans étape manuelle supplémentaire.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS utilisateurs (
            id SERIAL PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'lecture' CHECK (role IN ('lecture', 'tresorier', 'super_admin')),
            nom TEXT,
            prenom TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS membres (
            id SERIAL PRIMARY KEY,
            nom TEXT NOT NULL,
            telephone TEXT,
            cotisation_mensuelle NUMERIC(12,2) NOT NULL DEFAULT 50000
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS cotisations (
            id SERIAL PRIMARY KEY,
            membre_id INTEGER NOT NULL REFERENCES membres(id) ON DELETE CASCADE,
            montant NUMERIC(12,2) NOT NULL,
            date_cotisation DATE NOT NULL,
            mode_paiement TEXT NOT NULL
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS depenses (
            id SERIAL PRIMARY KEY,
            motif TEXT NOT NULL,
            montant NUMERIC(12,2) NOT NULL,
            categorie TEXT NOT NULL,
            date_depense DATE NOT NULL
        )
    """)
    conn.commit()

    # Compte super_admin (le propriétaire, toi) créé une seule fois, au tout
    # premier lancement. Le rôle trésorier n'est jamais créé automatiquement :
    # toi seul l'attribues depuis /utilisateurs, parmi les comptes inscrits.
    total_users = conn.execute("SELECT COUNT(*) AS total FROM utilisateurs").fetchone()["total"]
    if total_users == 0:
        admin_user = os.environ.get("ADMIN_USERNAME", "admin")
        admin_password = os.environ.get("ADMIN_PASSWORD")
        generated = False
        if not admin_password:
            admin_password = secrets.token_urlsafe(9)
            generated = True

        conn.execute("""
            INSERT INTO utilisateurs (username, password_hash, role)
            VALUES (?, ?, 'super_admin')
        """, (admin_user, generate_password_hash(admin_password)))
        conn.commit()

        if generated:
            print("=" * 60)
            print("Premier lancement : compte super_admin (propriétaire) créé.")
            print(f"  Identifiant : {admin_user}")
            print(f"  Mot de passe : {admin_password}")
            print("Notez-le et changez-le dès la première connexion.")
            print("=" * 60)

    # Données d'exemple uniquement lors de la toute première création.
    total_membres = conn.execute("SELECT COUNT(*) AS total FROM membres").fetchone()["total"]
    if total_membres == 0:
        membres_demo = [
            ("Barry Lewandowski", "+225 07 00 00 01", 50000),
            ("Amadou Koné", "+225 07 00 00 02", 50000),
            ("Koffi Alexis", "+225 07 00 00 03", 50000),
            ("Coulibaly Fatou", "+225 07 00 00 04", 50000),
        ]

        conn.executemany("""
            INSERT INTO membres (nom, telephone, cotisation_mensuelle)
            VALUES (?, ?, ?)
        """, membres_demo)

        cotisations_demo = [
            (1, 50000, "2026-08-05", "Orange Money"),
            (2, 25000, "2026-08-06", "Wave"),
            (3, 30000, "2026-08-10", "Espèces"),
            (4, 50000, "2026-08-15", "Orange Money"),
        ]

        conn.executemany("""
            INSERT INTO cotisations
            (membre_id, montant, date_cotisation, mode_paiement)
            VALUES (?, ?, ?, ?)
        """, cotisations_demo)

        depenses_demo = [
            ("Rafraîchissements et sonorisation", 45000, "Logistique", "2026-08-08"),
            ("Assistance membre (Mariage)", 25000, "Aide Sociale", "2026-08-14"),
            ("Impression registres & reçus", 18000, "Administration", "2026-08-19"),
        ]

        conn.executemany("""
            INSERT INTO depenses (motif, montant, categorie, date_depense)
            VALUES (?, ?, ?, ?)
        """, depenses_demo)

        conn.commit()

    conn.close()


def clean_phone(phone):
    if not phone:
        return ""
    return "".join(ch for ch in phone if ch.isdigit() or ch == "+")


def telephone_valide(telephone):
    """Validation simple : format international +indicatif suivi de 8 à 14
    chiffres (ex: +2250700000001). Pas de vérification que le numéro existe
    vraiment ou appartient à la personne — aucune vérification externe."""
    t = telephone.strip()
    if not t.startswith("+"):
        return False
    chiffres = t[1:]
    return chiffres.isdigit() and 8 <= len(chiffres) <= 14


# --- Authentification : routes ----------------------------------------------
@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("index"))

    if request.method == "POST":
        username = clean_phone(request.form.get("username") or "")
        password = request.form.get("password") or ""

        conn = get_db_connection()
        row = conn.execute(
            "SELECT * FROM utilisateurs WHERE username = ?", (username,)
        ).fetchone()
        conn.close()

        if row and check_password_hash(row["password_hash"], password):
            utilisateur = Utilisateur(row)
            login_user(utilisateur)
            flash(f"Bienvenue, {utilisateur.nom_complet} !", "success")
            next_page = request.args.get("next")
            return redirect(next_page or url_for("index"))

        flash("Identifiant ou mot de passe incorrect.", "danger")

    return render_template("login.html")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    flash("Vous avez été déconnecté.", "info")
    return redirect(url_for("login"))


@app.route("/inscription", methods=["GET", "POST"])
def inscription():
    if current_user.is_authenticated:
        return redirect(url_for("index"))

    if request.method == "POST":
        prenom = (request.form.get("prenom") or "").strip()
        nom = (request.form.get("nom") or "").strip()
        telephone = clean_phone(request.form.get("telephone") or "")
        password = request.form.get("password") or ""
        code_inscription = request.form.get("code_inscription") or ""

        if INSCRIPTION_CODE and code_inscription != INSCRIPTION_CODE:
            flash("Code d'inscription incorrect.", "danger")
            return render_template("inscription.html")

        if not prenom or not nom:
            flash("Prénom et nom sont obligatoires.", "danger")
            return render_template("inscription.html")

        if not telephone_valide(telephone):
            flash("Numéro de téléphone invalide. Format international requis, ex : +2250700000001.", "danger")
            return render_template("inscription.html")

        if len(password) < 8:
            flash("Le mot de passe doit faire au moins 8 caractères.", "danger")
            return render_template("inscription.html")

        # Tout le monde s'inscrit en rôle "lecture". Seul le super_admin
        # attribue le rôle trésorier ensuite, depuis /utilisateurs.
        conn = get_db_connection()
        try:
            row = conn.execute("""
                INSERT INTO utilisateurs (username, password_hash, role, nom, prenom)
                VALUES (?, ?, 'lecture', ?, ?)
                RETURNING id, username, role, nom, prenom
            """, (telephone, generate_password_hash(password), nom, prenom)).fetchone()
            conn.commit()
        except psycopg2.IntegrityError:
            conn.rollback()
            conn.close()
            flash("Un compte existe déjà avec ce numéro de téléphone.", "danger")
            return render_template("inscription.html")

        conn.close()

        login_user(Utilisateur(row))
        flash(f"Bienvenue, {prenom} ! Votre compte a été créé.", "success")
        return redirect(url_for("index"))

    return render_template("inscription.html")


@app.route("/changer_mot_de_passe", methods=["POST"])
@login_required
def changer_mot_de_passe():
    ancien = request.form.get("ancien_mot_de_passe") or ""
    nouveau = request.form.get("nouveau_mot_de_passe") or ""

    conn = get_db_connection()
    row = conn.execute(
        "SELECT * FROM utilisateurs WHERE id = ?", (current_user.id,)
    ).fetchone()

    if not row or not check_password_hash(row["password_hash"], ancien):
        conn.close()
        flash("Ancien mot de passe incorrect.", "danger")
        return redirect(url_for("index"))

    if len(nouveau) < 8:
        conn.close()
        flash("Le nouveau mot de passe doit faire au moins 8 caractères.", "danger")
        return redirect(url_for("index"))

    conn.execute(
        "UPDATE utilisateurs SET password_hash = ? WHERE id = ?",
        (generate_password_hash(nouveau), current_user.id),
    )
    conn.commit()
    conn.close()

    flash("Mot de passe mis à jour.", "success")
    return redirect(url_for("index"))


@app.route("/utilisateurs/ajouter", methods=["POST"])
@role_required("super_admin")
def ajouter_utilisateur():
    username = clean_phone(request.form.get("username") or "")
    password = request.form.get("password") or ""
    role = request.form.get("role") or "lecture"
    nom = (request.form.get("nom") or "").strip()
    prenom = (request.form.get("prenom") or "").strip()

    # Jamais "super_admin" depuis ce formulaire : un seul super_admin, créé
    # une seule fois au premier lancement.
    if role not in ("lecture", "tresorier"):
        role = "lecture"

    if not username or len(password) < 8:
        flash("Numéro requis et mot de passe d'au moins 8 caractères.", "danger")
        return redirect(url_for("utilisateurs"))

    conn = get_db_connection()
    try:
        conn.execute(
            "INSERT INTO utilisateurs (username, password_hash, role, nom, prenom) VALUES (?, ?, ?, ?, ?)",
            (username, generate_password_hash(password), role, nom, prenom),
        )
        conn.commit()
        flash("Utilisateur créé avec succès.", "success")
    except psycopg2.IntegrityError:
        conn.rollback()
        flash("Ce numéro existe déjà, ou le rôle trésorier est déjà pris.", "danger")
    finally:
        conn.close()

    return redirect(url_for("utilisateurs"))


@app.route("/utilisateurs")
@role_required("super_admin")
def utilisateurs():
    conn = get_db_connection()
    liste = conn.execute(
        "SELECT id, username, role, nom, prenom FROM utilisateurs ORDER BY LOWER(username) ASC"
    ).fetchall()
    conn.close()
    return render_template("utilisateurs.html", utilisateurs=liste)


@app.route("/utilisateurs/role/<int:user_id>", methods=["POST"])
@role_required("super_admin")
def changer_role_utilisateur(user_id):
    nouveau_role = request.form.get("role") or "lecture"

    if nouveau_role not in ("lecture", "tresorier"):
        flash("Rôle invalide.", "danger")
        return redirect(url_for("utilisateurs"))

    conn = get_db_connection()
    cible = conn.execute("SELECT role FROM utilisateurs WHERE id = ?", (user_id,)).fetchone()

    if not cible:
        conn.close()
        flash("Utilisateur introuvable.", "danger")
        return redirect(url_for("utilisateurs"))

    if cible["role"] == "super_admin":
        conn.close()
        flash("Le rôle du super_admin ne peut pas être modifié.", "danger")
        return redirect(url_for("utilisateurs"))

    try:
        conn.execute("UPDATE utilisateurs SET role = ? WHERE id = ?", (nouveau_role, user_id))
        conn.commit()
        flash("Rôle mis à jour.", "success")
    except psycopg2.IntegrityError:
        conn.rollback()
        flash("Impossible : le rôle trésorier est déjà attribué à quelqu'un d'autre.", "danger")
    finally:
        conn.close()

    return redirect(url_for("utilisateurs"))


@app.route("/utilisateurs/supprimer/<int:user_id>", methods=["POST"])
@role_required("super_admin")
def supprimer_utilisateur(user_id):
    if str(user_id) == current_user.id:
        flash("Vous ne pouvez pas supprimer votre propre compte.", "danger")
        return redirect(url_for("utilisateurs"))

    conn = get_db_connection()

    cible = conn.execute(
        "SELECT role FROM utilisateurs WHERE id = ?", (user_id,)
    ).fetchone()

    if cible and cible["role"] == "super_admin":
        conn.close()
        flash("Le compte super_admin ne peut pas être supprimé.", "danger")
        return redirect(url_for("utilisateurs"))

    conn.execute("DELETE FROM utilisateurs WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()

    flash("Utilisateur supprimé.", "info")
    return redirect(url_for("utilisateurs"))


# --- Application : routes protégées -----------------------------------------
@app.route("/")
@login_required
def index():
    conn = get_db_connection()

    total_epargne = conn.execute(
        "SELECT COALESCE(SUM(montant), 0) AS total FROM cotisations"
    ).fetchone()["total"]

    total_depenses = conn.execute(
        "SELECT COALESCE(SUM(montant), 0) AS total FROM depenses"
    ).fetchone()["total"]

    solde_net = total_epargne - total_depenses

    membres = conn.execute("""
        SELECT
            m.*,
            COALESCE(SUM(c.montant), 0) AS total_paye,
            COUNT(c.id) AS nombre_paiements,
            MAX(c.date_cotisation) AS dernier_paiement
        FROM membres m
        LEFT JOIN cotisations c ON c.membre_id = m.id
        GROUP BY m.id
        ORDER BY LOWER(m.nom) ASC
    """).fetchall()

    cotisations = conn.execute("""
        SELECT c.*, m.nom
        FROM cotisations c
        JOIN membres m ON c.membre_id = m.id
        ORDER BY c.date_cotisation DESC, c.id DESC
        LIMIT 10
    """).fetchall()

    depenses = conn.execute("""
        SELECT *
        FROM depenses
        ORDER BY date_depense DESC, id DESC
        LIMIT 10
    """).fetchall()

    conn.close()

    return render_template(
        "index.html",
        total_epargne=total_epargne,
        total_depenses=total_depenses,
        solde_net=solde_net,
        membres=membres,
        cotisations=cotisations,
        depenses=depenses,
        today=datetime.now().strftime("%Y-%m-%d"),
    )


@app.route("/ajouter_cotisation", methods=["POST"])
@role_required("tresorier", "super_admin")
def ajouter_cotisation():
    membre_id = request.form.get("membre_id")
    montant_raw = request.form.get("montant")
    mode = request.form.get("mode_paiement")
    date_c = request.form.get("date_cotisation") or datetime.now().strftime("%Y-%m-%d")

    try:
        montant = float(montant_raw)
    except (TypeError, ValueError):
        flash("Montant de cotisation invalide.", "danger")
        return redirect(url_for("index"))

    if not membre_id or not mode or montant <= 0:
        flash("Veuillez remplir correctement tous les champs.", "danger")
        return redirect(url_for("index"))

    conn = get_db_connection()
    membre = conn.execute("SELECT id FROM membres WHERE id = ?", (membre_id,)).fetchone()

    if not membre:
        conn.close()
        flash("Membre introuvable.", "danger")
        return redirect(url_for("index"))

    conn.execute("""
        INSERT INTO cotisations
        (membre_id, montant, date_cotisation, mode_paiement)
        VALUES (?, ?, ?, ?)
    """, (membre_id, montant, date_c, mode))

    conn.commit()
    conn.close()

    flash("Cotisation enregistrée avec succès !", "success")
    return redirect(url_for("index"))


@app.route("/modifier_cotisation/<int:cotisation_id>", methods=["POST"])
@role_required("tresorier", "super_admin")
def modifier_cotisation(cotisation_id):
    montant_raw = request.form.get("montant")
    mode = request.form.get("mode_paiement")
    date_c = request.form.get("date_cotisation")

    try:
        montant = float(montant_raw)
    except (TypeError, ValueError):
        flash("Montant invalide.", "danger")
        return redirect(url_for("index"))

    if montant <= 0 or not mode or not date_c:
        flash("Données de paiement invalides.", "danger")
        return redirect(url_for("index"))

    conn = get_db_connection()
    conn.execute("""
        UPDATE cotisations
        SET montant = ?, mode_paiement = ?, date_cotisation = ?
        WHERE id = ?
    """, (montant, mode, date_c, cotisation_id))
    conn.commit()
    conn.close()

    flash("Paiement modifié.", "success")
    return redirect(url_for("index"))


@app.route("/supprimer_cotisation/<int:cotisation_id>", methods=["POST"])
@role_required("tresorier", "super_admin")
def supprimer_cotisation(cotisation_id):
    conn = get_db_connection()
    conn.execute("DELETE FROM cotisations WHERE id = ?", (cotisation_id,))
    conn.commit()
    conn.close()

    flash("Paiement supprimé.", "info")
    return redirect(url_for("index"))


@app.route("/ajouter_depense", methods=["POST"])
@role_required("tresorier", "super_admin")
def ajouter_depense():
    motif = request.form.get("motif")
    montant_raw = request.form.get("montant")
    categorie = request.form.get("categorie")
    date_d = request.form.get("date_depense") or datetime.now().strftime("%Y-%m-%d")

    try:
        montant = float(montant_raw)
    except (TypeError, ValueError):
        flash("Montant de dépense invalide.", "danger")
        return redirect(url_for("index"))

    if not motif or not categorie or montant <= 0:
        flash("Veuillez remplir correctement tous les champs.", "danger")
        return redirect(url_for("index"))

    conn = get_db_connection()
    conn.execute("""
        INSERT INTO depenses (motif, montant, categorie, date_depense)
        VALUES (?, ?, ?, ?)
    """, (motif, montant, categorie, date_d))

    conn.commit()
    conn.close()

    flash("Dépense enregistrée avec succès !", "danger")
    return redirect(url_for("index"))


@app.route("/supprimer_depense/<int:depense_id>", methods=["POST"])
@role_required("tresorier", "super_admin")
def supprimer_depense(depense_id):
    conn = get_db_connection()
    conn.execute("DELETE FROM depenses WHERE id = ?", (depense_id,))
    conn.commit()
    conn.close()

    flash("Dépense supprimée.", "info")
    return redirect(url_for("index"))


@app.route("/ajouter_membre", methods=["POST"])
@role_required("tresorier", "super_admin")
def ajouter_membre():
    nom = (request.form.get("nom") or "").strip()
    telephone = (request.form.get("telephone") or "").strip()
    cotisation_raw = request.form.get("cotisation_mensuelle") or "50000"

    try:
        cotisation = float(cotisation_raw)
    except (TypeError, ValueError):
        cotisation = 50000

    if not nom:
        flash("Le nom du membre est obligatoire.", "danger")
        return redirect(url_for("index"))

    if cotisation < 0:
        flash("La cotisation mensuelle ne peut pas être négative.", "danger")
        return redirect(url_for("index"))

    conn = get_db_connection()
    conn.execute("""
        INSERT INTO membres (nom, telephone, cotisation_mensuelle)
        VALUES (?, ?, ?)
    """, (nom, telephone, cotisation))

    conn.commit()
    conn.close()

    flash("Membre ajouté avec succès !", "success")
    return redirect(url_for("index"))


@app.route("/modifier_membre/<int:membre_id>", methods=["POST"])
@role_required("tresorier", "super_admin")
def modifier_membre(membre_id):
    nom = (request.form.get("nom") or "").strip()
    telephone = (request.form.get("telephone") or "").strip()
    cotisation_raw = request.form.get("cotisation_mensuelle") or "50000"

    try:
        cotisation = float(cotisation_raw)
    except (TypeError, ValueError):
        cotisation = 50000

    if not nom or cotisation < 0:
        flash("Informations du membre invalides.", "danger")
        return redirect(url_for("index"))

    conn = get_db_connection()
    conn.execute("""
        UPDATE membres
        SET nom = ?, telephone = ?, cotisation_mensuelle = ?
        WHERE id = ?
    """, (nom, telephone, cotisation, membre_id))

    conn.commit()
    conn.close()

    flash("Membre modifié avec succès.", "success")
    return redirect(url_for("index"))


@app.route("/supprimer_membre/<int:membre_id>", methods=["POST"])
@role_required("tresorier", "super_admin")
def supprimer_membre(membre_id):
    conn = get_db_connection()

    # La contrainte ON DELETE CASCADE sur cotisations.membre_id s'occupe
    # déjà de l'historique, mais on garde l'étape explicite pour rester
    # portable si jamais la contrainte venait à changer.
    conn.execute("DELETE FROM cotisations WHERE membre_id = ?", (membre_id,))
    conn.execute("DELETE FROM membres WHERE id = ?", (membre_id,))

    conn.commit()
    conn.close()

    flash("Membre et son historique ont été supprimés.", "info")
    return redirect(url_for("index"))


@app.route("/api/membre/<int:membre_id>")
@login_required
def api_membre(membre_id):
    conn = get_db_connection()

    membre = conn.execute("""
        SELECT
            m.*,
            COALESCE(SUM(c.montant), 0) AS total_paye,
            COUNT(c.id) AS nombre_paiements,
            MAX(c.date_cotisation) AS dernier_paiement
        FROM membres m
        LEFT JOIN cotisations c ON c.membre_id = m.id
        WHERE m.id = ?
        GROUP BY m.id
    """, (membre_id,)).fetchone()

    if not membre:
        conn.close()
        return jsonify({"error": "Membre introuvable"}), 404

    paiements = conn.execute("""
        SELECT id, montant, date_cotisation, mode_paiement
        FROM cotisations
        WHERE membre_id = ?
        ORDER BY date_cotisation DESC, id DESC
    """, (membre_id,)).fetchall()

    conn.close()

    return jsonify({
        "id": membre["id"],
        "nom": membre["nom"],
        "telephone": membre["telephone"] or "",
        "cotisation_mensuelle": float(membre["cotisation_mensuelle"]),
        "total_paye": float(membre["total_paye"]),
        "nombre_paiements": membre["nombre_paiements"],
        "dernier_paiement": membre["dernier_paiement"].isoformat() if membre["dernier_paiement"] else "",
        "peut_modifier": current_user.role in ("tresorier", "super_admin"),
        "paiements": [
            {
                "id": p["id"],
                "montant": float(p["montant"]),
                "date": p["date_cotisation"].isoformat(),
                "mode": p["mode_paiement"],
            }
            for p in paiements
        ],
    })


@app.route("/api/historique")
@login_required
def api_historique():
    conn = get_db_connection()
    paiements = conn.execute("""
        SELECT
            c.id,
            c.membre_id,
            m.nom,
            c.montant,
            c.date_cotisation,
            c.mode_paiement
        FROM cotisations c
        JOIN membres m ON m.id = c.membre_id
        ORDER BY c.date_cotisation DESC, c.id DESC
    """).fetchall()
    conn.close()

    return jsonify([
        {
            "id": p["id"],
            "membre_id": p["membre_id"],
            "nom": p["nom"],
            "montant": float(p["montant"]),
            "date": p["date_cotisation"].isoformat(),
            "mode": p["mode_paiement"],
        }
        for p in paiements
    ])


@app.route("/api/toutes-depenses")
@login_required
def api_toutes_depenses():
    conn = get_db_connection()
    depenses = conn.execute("""
        SELECT id, motif, montant, categorie, date_depense
        FROM depenses
        ORDER BY date_depense DESC, id DESC
    """).fetchall()
    conn.close()

    return jsonify([
        {
            "id": d["id"],
            "motif": d["motif"],
            "categorie": d["categorie"],
            "montant": float(d["montant"]),
            "date": d["date_depense"].isoformat(),
        }
        for d in depenses
    ])


# init_db() n'est PAS appelée automatiquement au chargement du module.
# Les tables et le compte super_admin existent déjà sur Supabase — l'appeler
# à chaque démarrage à froid de la fonction (Vercel) ajoutait plusieurs
# allers-retours réseau vers la base (Europe) depuis la fonction (États-Unis),
# assez pour faire échouer les robots stricts comme celui de PWABuilder.
# Pour ré-initialiser un schéma vierge (nouveau projet Supabase), lance
# `python app.py` en local une fois : le bloc ci-dessous l'appelle alors.

if __name__ == "__main__":
    init_db()
    # host="127.0.0.1" = accessible uniquement en local. Pour un vrai
    # déploiement, utilisez Gunicorn (voir Procfile) derrière un reverse
    # proxy HTTPS, jamais app.run() en production.
    app.run(host="127.0.0.1", port=5050, debug=DEBUG_MODE)
