import sqlite3
import os
import json
from datetime import datetime
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, flash, g
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "hayder-solution-dev-secret-change-me")
APP_NAME = os.environ.get("APP_NAME", "Hayder Solution")

DATABASE = os.path.join(os.path.dirname(__file__), "hayder.db")
DEAL_STAGES = ["New", "Contacted", "Qualified", "Proposal", "Won", "Lost"]


@app.context_processor
def inject_globals():
    return {"app_name": APP_NAME}


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    first_time = not os.path.exists(DATABASE)
    db = sqlite3.connect(DATABASE)
    db.row_factory = sqlite3.Row
    with open(os.path.join(os.path.dirname(__file__), "schema.sql")) as f:
        db.executescript(f.read())
    if first_time:
        db.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
            ("admin", generate_password_hash("admin123"), "admin"),
        )
        db.commit()
    ai_row = db.execute("SELECT id FROM ai_settings LIMIT 1").fetchone()
    if not ai_row:
        default_prompt = (
            "Aap Hayder Solution ke assistant hain. Leads ke sawalon ka Roman Urdu mein "
            "dosti aur ikhtisar se jawab dein aur unhe qualify karne mein madad karein."
        )
        db.execute("INSERT INTO ai_settings (system_prompt, enabled) VALUES (?, 1)", (default_prompt,))
        db.commit()
    db.close()


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapped


# ---------------- AUTH ----------------
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"]
        invite_code = request.form.get("invite_code", "")
        required_code = os.environ.get("REGISTRATION_CODE", "hayder123")

        if invite_code != required_code:
            flash("Ghalat invite code.", "danger")
            return render_template("register.html")
        if not username or not password:
            flash("Username aur password zaroori hain", "danger")
            return render_template("register.html")

        db = get_db()
        existing = db.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
        if existing:
            flash("Yeh username pehle se maujood hai", "danger")
            return render_template("register.html")

        db.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
            (username, generate_password_hash(password), "staff"),
        )
        db.commit()
        flash("Account ban gaya! Ab login karein.", "success")
        return redirect(url_for("login"))
    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"]
        db = get_db()
        user = db.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        if user and check_password_hash(user["password_hash"], password):
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            return redirect(url_for("dashboard"))
        flash("Ghalat username ya password", "danger")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------------- DASHBOARD ----------------
@app.route("/")
@login_required
def dashboard():
    db = get_db()
    total_contacts = db.execute("SELECT COUNT(*) c FROM contacts").fetchone()["c"]
    total_deals = db.execute("SELECT COUNT(*) c FROM deals").fetchone()["c"]
    pipeline_value = db.execute(
        "SELECT COALESCE(SUM(value),0) s FROM deals WHERE stage NOT IN ('Won','Lost')"
    ).fetchone()["s"]
    won_value = db.execute(
        "SELECT COALESCE(SUM(value),0) s FROM deals WHERE stage = 'Won'"
    ).fetchone()["s"]
    recent_activities = db.execute(
        """SELECT a.*, c.name as contact_name FROM activities a
           JOIN contacts c ON a.contact_id = c.id
           ORDER BY a.created_at DESC LIMIT 6"""
    ).fetchall()
    return render_template(
        "dashboard.html",
        total_contacts=total_contacts,
        total_deals=total_deals,
        pipeline_value=pipeline_value,
        won_value=won_value,
        recent_activities=recent_activities,
    )


# ---------------- CONTACTS ----------------
@app.route("/contacts", methods=["GET", "POST"])
@login_required
def contacts():
    db = get_db()
    if request.method == "POST":
        db.execute(
            "INSERT INTO contacts (name, company, email, phone, source, notes) VALUES (?, ?, ?, ?, ?, ?)",
            (
                request.form["name"],
                request.form.get("company"),
                request.form.get("email"),
                request.form.get("phone"),
                request.form.get("source"),
                request.form.get("notes"),
            ),
        )
        db.commit()
        flash("Contact add ho gaya", "success")
        return redirect(url_for("contacts"))
    search = request.args.get("q", "")
    if search:
        rows = db.execute(
            "SELECT * FROM contacts WHERE name LIKE ? OR company LIKE ? OR phone LIKE ? ORDER BY id DESC",
            (f"%{search}%", f"%{search}%", f"%{search}%"),
        ).fetchall()
    else:
        rows = db.execute("SELECT * FROM contacts ORDER BY id DESC").fetchall()
    return render_template("contacts.html", contacts=rows, search=search)


@app.route("/contacts/<int:cid>")
@login_required
def contact_detail(cid):
    db = get_db()
    contact = db.execute("SELECT * FROM contacts WHERE id = ?", (cid,)).fetchone()
    deals_rows = db.execute(
        "SELECT * FROM deals WHERE contact_id = ? ORDER BY created_at DESC", (cid,)
    ).fetchall()
    activities_rows = db.execute(
        "SELECT * FROM activities WHERE contact_id = ? ORDER BY created_at DESC", (cid,)
    ).fetchall()
    return render_template(
        "contact_detail.html",
        contact=contact,
        deals=deals_rows,
        activities=activities_rows,
        stages=DEAL_STAGES,
    )


@app.route("/contacts/<int:cid>/delete", methods=["POST"])
@login_required
def delete_contact(cid):
    db = get_db()
    db.execute("DELETE FROM contacts WHERE id = ?", (cid,))
    db.commit()
    flash("Contact delete ho gaya", "info")
    return redirect(url_for("contacts"))


@app.route("/contacts/<int:cid>/activity", methods=["POST"])
@login_required
def add_activity(cid):
    db = get_db()
    db.execute(
        "INSERT INTO activities (contact_id, type, content) VALUES (?, ?, ?)",
        (cid, request.form.get("type", "note"), request.form["content"]),
    )
    db.commit()
    return redirect(url_for("contact_detail", cid=cid))


@app.route("/contacts/<int:cid>/deal", methods=["POST"])
@login_required
def add_deal(cid):
    db = get_db()
    db.execute(
        "INSERT INTO deals (contact_id, title, value, stage) VALUES (?, ?, ?, 'New')",
        (cid, request.form["title"], request.form.get("value") or 0),
    )
    db.commit()
    flash("Deal add ho gaya", "success")
    return redirect(url_for("contact_detail", cid=cid))


# ---------------- PIPELINE (Kanban) ----------------
@app.route("/pipeline")
@login_required
def pipeline():
    db = get_db()
    columns = {}
    for stage in DEAL_STAGES:
        rows = db.execute(
            """SELECT d.*, c.name as contact_name FROM deals d
               JOIN contacts c ON d.contact_id = c.id
               WHERE d.stage = ? ORDER BY d.created_at DESC""",
            (stage,),
        ).fetchall()
        columns[stage] = rows
    return render_template("pipeline.html", columns=columns, stages=DEAL_STAGES)


@app.route("/pipeline/<int:did>/move/<string:stage>", methods=["POST"])
@login_required
def pipeline_move(did, stage):
    if stage not in DEAL_STAGES:
        return redirect(url_for("pipeline"))
    db = get_db()
    db.execute("UPDATE deals SET stage = ? WHERE id = ?", (stage, did))
    db.commit()
    return redirect(url_for("pipeline"))


# ---------------- AI SETTINGS ----------------
@app.route("/ai-settings", methods=["GET", "POST"])
@login_required
def ai_settings_page():
    db = get_db()
    if request.method == "POST":
        prompt = request.form["system_prompt"]
        enabled = 1 if request.form.get("enabled") == "on" else 0
        existing = db.execute("SELECT id FROM ai_settings ORDER BY id DESC LIMIT 1").fetchone()
        if existing:
            db.execute(
                "UPDATE ai_settings SET system_prompt=?, enabled=? WHERE id=?",
                (prompt, enabled, existing["id"]),
            )
        else:
            db.execute("INSERT INTO ai_settings (system_prompt, enabled) VALUES (?, ?)", (prompt, enabled))
        db.commit()
        flash("AI settings save ho gayin", "success")
        return redirect(url_for("ai_settings_page"))
    settings = db.execute("SELECT * FROM ai_settings ORDER BY id DESC LIMIT 1").fetchone()
    return render_template("ai_settings.html", settings=settings)


@app.route("/privacy")
def privacy_policy():
    return render_template("privacy.html")


# ---------------- REPORTS ----------------
@app.route("/reports")
@login_required
def reports():
    db = get_db()
    stage_rows = db.execute("SELECT stage, COUNT(*) c FROM deals GROUP BY stage").fetchall()
    stage_labels = [r["stage"] for r in stage_rows]
    stage_counts = [r["c"] for r in stage_rows]

    value_rows = db.execute(
        "SELECT stage, COALESCE(SUM(value),0) v FROM deals GROUP BY stage"
    ).fetchall()
    value_labels = [r["stage"] for r in value_rows]
    value_totals = [r["v"] for r in value_rows]

    contact_rows = db.execute(
        "SELECT strftime('%Y-%m', created_at) ym, COUNT(*) c FROM contacts GROUP BY ym ORDER BY ym"
    ).fetchall()
    contact_labels = [r["ym"] for r in contact_rows]
    contact_counts = [r["c"] for r in contact_rows]

    total_pipeline_value = db.execute(
        "SELECT COALESCE(SUM(value),0) s FROM deals WHERE stage NOT IN ('Won','Lost')"
    ).fetchone()["s"]

    return render_template(
        "reports.html",
        stage_labels=json.dumps(stage_labels),
        stage_counts=json.dumps(stage_counts),
        value_labels=json.dumps(value_labels),
        value_totals=json.dumps(value_totals),
        contact_labels=json.dumps(contact_labels),
        contact_counts=json.dumps(contact_counts),
        total_pipeline_value=total_pipeline_value,
    )


if __name__ == "__main__":
    init_db()
    app.run(debug=True, host="0.0.0.0", port=5000)
else:
    init_db()
