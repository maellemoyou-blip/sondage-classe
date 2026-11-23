"""Sondage de la classe : un petit formulaire web prêt pour le cloud.

- Sans variable DATABASE_URL  -> base SQLite locale (pour tester sur son PC)
- Avec DATABASE_URL           -> base PostgreSQL distante (Neon) pour le cloud
"""
import csv
import hmac
import io
import os

from flask import Flask, Response, abort, redirect, render_template, request, url_for

app = Flask(__name__)

# --------------------------------------------------------------------------
# 1. Configuration (lue dans les variables d'environnement, jamais dans le code)
# --------------------------------------------------------------------------
DATABASE_URL = os.environ.get("DATABASE_URL")
ADMIN_KEY = os.environ.get("ADMIN_KEY")          # protège l'export CSV
USE_PG = bool(DATABASE_URL)

if USE_PG:
    import psycopg2

    PH = "%s"                                      # marqueur de paramètre PostgreSQL
else:
    import sqlite3

    PH = "?"                                       # marqueur de paramètre SQLite

FILIERES = ["Informatique", "Mathématiques", "Physique", "Lettres", "Autre"]
NIVEAUX = ["Débutant", "Intermédiaire", "Avancé"]
TECHNOS = ["Python", "JavaScript", "PHP", "Java", "Autre"]


# --------------------------------------------------------------------------
# 2. Accès à la base de données
# --------------------------------------------------------------------------
def connexion():
    if USE_PG:
        return psycopg2.connect(DATABASE_URL, connect_timeout=15)
    return sqlite3.connect(os.environ.get("SQLITE_PATH", "sondage.db"))


def executer(sql, params=(), lire=False):
    """Ouvre une connexion, exécute une requête paramétrée, referme."""
    conn = connexion()
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        lignes = cur.fetchall() if lire else None
        conn.commit()
        return lignes
    finally:
        conn.close()


_pret = False


def preparer_base():
    """Crée la table au premier besoin (et pas à l'import : l'appli démarre
    même si la base dort encore)."""
    global _pret
    if _pret:
        return
    cle = "SERIAL PRIMARY KEY" if USE_PG else "INTEGER PRIMARY KEY AUTOINCREMENT"
    executer(
        f"""CREATE TABLE IF NOT EXISTS reponses (
                id {cle},
                prenom TEXT NOT NULL,
                filiere TEXT NOT NULL,
                niveau TEXT NOT NULL,
                techno TEXT NOT NULL,
                avis TEXT,
                cree_le TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )"""
    )
    _pret = True


# --------------------------------------------------------------------------
# 3. Validation des données (ne jamais faire confiance au navigateur)
# --------------------------------------------------------------------------
def valider(form):
    donnees = {
        "prenom": form.get("prenom", "").strip(),
        "filiere": form.get("filiere", ""),
        "niveau": form.get("niveau", ""),
        "techno": form.get("techno", ""),
        "avis": form.get("avis", "").strip(),
    }
    erreurs = []
    if not 2 <= len(donnees["prenom"]) <= 50:
        erreurs.append("Le prénom doit contenir entre 2 et 50 caractères.")
    if donnees["filiere"] not in FILIERES:
        erreurs.append("Choisissez une filière dans la liste.")
    if donnees["niveau"] not in NIVEAUX:
        erreurs.append("Choisissez votre niveau.")
    if donnees["techno"] not in TECHNOS:
        erreurs.append("Choisissez une technologie préférée.")
    if len(donnees["avis"]) > 300:
        erreurs.append("Votre avis ne doit pas dépasser 300 caractères.")
    return donnees, erreurs


# --------------------------------------------------------------------------
# 4. Les pages
# --------------------------------------------------------------------------
@app.route("/", methods=["GET", "POST"])
def accueil():
    if request.method == "POST":
        if request.form.get("site_web"):            # champ-piège invisible : robot
            return redirect(url_for("merci"))
        donnees, erreurs = valider(request.form)
        if erreurs:
            return (
                render_template("index.html", erreurs=erreurs, d=donnees, listes=listes()),
                400,
            )
        preparer_base()
        executer(
            f"INSERT INTO reponses (prenom, filiere, niveau, techno, avis) "
            f"VALUES ({PH}, {PH}, {PH}, {PH}, {PH})",
            (donnees["prenom"], donnees["filiere"], donnees["niveau"],
             donnees["techno"], donnees["avis"]),
        )
        return redirect(url_for("merci"))          # évite le double envoi (F5)
    return render_template("index.html", erreurs=[], d={}, listes=listes())


@app.route("/merci")
def merci():
    return render_template("merci.html")


@app.route("/resultats")
def resultats():
    preparer_base()
    total = executer("SELECT COUNT(*) FROM reponses", lire=True)[0][0]
    par_techno = executer(
        "SELECT techno, COUNT(*) FROM reponses GROUP BY techno ORDER BY COUNT(*) DESC",
        lire=True,
    )
    derniers = executer(
        "SELECT prenom, techno, avis FROM reponses ORDER BY id DESC LIMIT 10",
        lire=True,
    )
    plus_grand = max([n for _, n in par_techno], default=1)
    return render_template(
        "resultats.html", total=total, par_techno=par_techno,
        derniers=derniers, plus_grand=plus_grand,
    )


@app.route("/admin/export.csv")
def export_csv():
    cle = request.args.get("cle", "")
    # 404 si l'export n'est pas configuré ; comparaison à temps constant sinon
    if not ADMIN_KEY or not hmac.compare_digest(cle, ADMIN_KEY):
        abort(404)
    preparer_base()
    lignes = executer(
        "SELECT id, prenom, filiere, niveau, techno, avis, cree_le "
        "FROM reponses ORDER BY id",
        lire=True,
    )
    sortie = io.StringIO()
    w = csv.writer(sortie)
    w.writerow(["id", "prenom", "filiere", "niveau", "techno", "avis", "cree_le"])
    for ligne in lignes:
        # neutralise les "injections de formules" si on ouvre le CSV dans Excel
        w.writerow([("'" + str(c)) if str(c).startswith(("=", "+", "-", "@")) else c
                    for c in ligne])
    return Response(
        sortie.getvalue(), mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=reponses.csv"},
    )


@app.route("/sante")
def sante():
    """Page de diagnostic : l'application voit-elle sa base ?"""
    try:
        preparer_base()
        n = executer("SELECT COUNT(*) FROM reponses", lire=True)[0][0]
        return {"statut": "ok", "base": "postgresql" if USE_PG else "sqlite", "reponses": n}
    except Exception as e:                         # affichage pédagogique
        return {"statut": "erreur", "detail": type(e).__name__}, 500


def listes():
    return {"filieres": FILIERES, "niveaux": NIVEAUX, "technos": TECHNOS}


if __name__ == "__main__":                         # lancement local : python app.py
    app.run(debug=True)
