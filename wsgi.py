import os

# --- Variables d'environnement -----------------------------------------
# Clé secrète Flask : déjà générée aléatoirement pour toi, ne la change pas
# sauf si tu en regénères une autre (python3 -c "import secrets; print(secrets.token_hex(32))").
os.environ['SECRET_KEY'] = '44f6f1e53124370112a28b9ce829be4d6c874a970bffd0fcbeba769026cd3f78'

# Chaîne de connexion Supabase (pooler transaction, IPv4) : remplace
# UNIQUEMENT "TON-MOT-DE-PASSE-ICI" par le mot de passe de la base
# (Supabase > bouton Connect > onglet "Transaction pooler").
os.environ['DATABASE_URL'] = 'postgresql://postgres.wadfdlorhrijbzzgpalh:TON-MOT-DE-PASSE-ICI@aws-1-eu-west-1.pooler.supabase.com:6543/postgres'

# Compte super_admin (le propriétaire, toi) créé automatiquement au premier
# lancement. Choisis toi-même un mot de passe fort ici (8 caractères minimum).
os.environ['ADMIN_USERNAME'] = 'admin'
os.environ['ADMIN_PASSWORD'] = 'CHANGE-CE-MOT-DE-PASSE-ICI'

# Code que tu donnes aux vrais membres pour qu'ils puissent s'inscrire.
# Change-le, et ne le publie pas ailleurs que directement aux membres.
os.environ['INSCRIPTION_CODE'] = 'CHANGE-CE-CODE-ICI'

# Ne jamais mettre à '1' en ligne.
os.environ['FLASK_DEBUG'] = '0'

# --- Chargement de l'application ----------------------------------------
import sys

# Remplace "tonpseudo" par ton nom d'utilisateur PythonAnywhere
# (visible en haut à droite du dashboard PythonAnywhere).
path = '/home/tonpseudo/caisse'
if path not in sys.path:
    sys.path.append(path)

from app import app as application
