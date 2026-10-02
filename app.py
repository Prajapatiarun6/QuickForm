
import io
import json
import os
import re
import secrets
from functools import wraps

from flask import (
    Flask, render_template, request, redirect, jsonify,
    send_file, session, abort
)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.middleware.proxy_fix import ProxyFix

import firebase_admin
from firebase_admin import credentials, firestore, auth
import pandas as pd


app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)

# Safe defaults for browser sessions.
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    MAX_CONTENT_LENGTH=2 * 1024 * 1024,
)

app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)


# ---------------------------------------------------------------------------
# Firebase
# ---------------------------------------------------------------------------

if not firebase_admin._apps:
    if os.path.exists("firebase_key.json"):
        cred = credentials.Certificate("firebase_key.json")
        firebase_admin.initialize_app(cred)
    else:
        raw = os.environ.get("FIREBASE_CONFIG_JSON", "").strip()
        if raw:
            cred_json = json.loads(raw)
            firebase_admin.initialize_app(credentials.Certificate(cred_json))

db = firestore.client() if firebase_admin._apps else None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def clean_email(value):
    return str(value or "").strip().lower()


def is_valid_email(email):
    return bool(EMAIL_RE.fullmatch(email))


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_email" not in session or db is None:
            return redirect("/")
        return view(*args, **kwargs)
    return wrapped


def csrf_token():
    token = session.get("_csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf_token"] = token
    return token


app.jinja_env.globals["csrf_token"] = csrf_token


def display_response_value(field, value):
    """Presentation-only formatting; Firestore values remain untouched."""
    text = "" if value is None else str(value)
    label = str(field or "").strip().casefold()
    name_like = (
        label == "name"
        or "full name" in label
        or "student name" in label
        or label.endswith(" name")
        or label.startswith("name ")
    )
    return text.title() if name_like and text else text


app.jinja_env.globals["display_response_value"] = display_response_value


def require_csrf():
    supplied = request.form.get("_csrf_token", "")
    expected = session.get("_csrf_token", "")
    if not expected or not supplied or not secrets.compare_digest(supplied, expected):
        abort(400, description="Invalid request token.")


def get_owned_form(form_id):
    if not db or "user_email" not in session:
        return None

    doc = db.collection("forms").document(form_id).get()
    if not doc.exists:
        return None

    data = doc.to_dict()
    if data.get("user_id") != session["user_email"]:
        return None

    return doc


def get_existing_db_fields(form_id):
    keys = set()
    if db and form_id:
        responses = (
            db.collection("forms")
            .document(form_id)
            .collection("responses")
            .limit(10)
            .stream()
        )
        for response in responses:
            keys.update(response.to_dict().keys())
    return keys


def get_primary_identifier(fields, form_id=None):
    """Return the administrator-selected unique identifier when available.

    Older forms that do not have the new metadata keep the original
    keyword-based fallback so existing forms continue to work.
    """
    if not fields:
        return None

    if form_id and db:
        form_doc = db.collection("forms").document(form_id).get()
        if form_doc.exists:
            selected = str(form_doc.to_dict().get("unique_identifier", "")).strip()
            if selected and selected in fields:
                return selected

    existing_keys = get_existing_db_fields(form_id)
    candidates = [f for f in fields if f in existing_keys] if existing_keys else list(fields)

    preferred = ("phone", "mobile", "email", "enroll", "roll", "student id", "id", "name")
    for keyword in preferred:
        for field in candidates:
            if keyword in field.lower():
                return field

    return candidates[0] if candidates else fields[0]


def normalize_value(value):
    return str(value or "").strip().casefold()


def find_response_by_identifier(form_id, primary_field, primary_value, secondary_field=None, secondary_value=None):
    """
    Match by the actual identifier field instead of searching every value in
    the record. This prevents unrelated records from being overwritten.
    """
    if not db or not primary_field or not primary_value:
        return None

    responses = (
        db.collection("forms")
        .document(form_id)
        .collection("responses")
        .stream()
    )

    primary_value = normalize_value(primary_value)
    secondary_value = normalize_value(secondary_value)

    primary_matches = []

    for doc in responses:
        data = doc.to_dict()
        if normalize_value(data.get(primary_field)) == primary_value:
            primary_matches.append(doc)

    if secondary_value:
        for doc in primary_matches:
            data = doc.to_dict()
            if secondary_field and normalize_value(data.get(secondary_field)) == secondary_value:
                return doc

            # Backward-compatible fallback: if the second field is not known,
            # check the other stored values.
            if not secondary_field:
                other_values = [
                    normalize_value(v)
                    for k, v in data.items()
                    if k != primary_field
                ]
                if secondary_value in other_values:
                    return doc

    return primary_matches[0] if len(primary_matches) == 1 else None


def sync_firebase_password(email, password):
    """Keep the existing QuickForm Firestore login while mirroring the
    credentials into Firebase Auth so password-reset email can work."""
    try:
        user = auth.get_user_by_email(email)
        auth.update_user(user.uid, password=password)
    except auth.UserNotFoundError:
        try:
            auth.create_user(email=email, password=password, email_verified=False)
        except Exception:
            pass
    except Exception:
        pass


@app.route("/auth/firebase", methods=["POST"])
def firebase_session():
    if not db:
        return jsonify({"ok": False, "error": "Firebase database is not configured."}), 500

    payload = request.get_json(silent=True) or {}
    token = str(payload.get("id_token", "")).strip()
    if not token:
        return jsonify({"ok": False, "error": "Missing Firebase token."}), 400

    try:
        decoded = auth.verify_id_token(token)
        email = clean_email(decoded.get("email"))
        if not email or not is_valid_email(email):
            return jsonify({"ok": False, "error": "Google account has no usable email."}), 400

        user_ref = db.collection("users").document(email)
        user_doc = user_ref.get()
        if not user_doc.exists:
            user_ref.set({
                "file_prefix": "QuickForm",
                "auth_provider": "google",
                "created_at": firestore.SERVER_TIMESTAMP
            })
        else:
            user_ref.set({"auth_provider": "google"}, merge=True)

        session.clear()
        session["user_email"] = email
        session["_csrf_token"] = secrets.token_urlsafe(32)
        return jsonify({"ok": True})
    except Exception:
        return jsonify({"ok": False, "error": "Google sign-in could not be verified."}), 401


# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------

@app.route("/")
def admin_panel():
    if "user_email" not in session:
        return render_template("admin.html", view="login")

    if not db:
        return render_template(
            "admin.html",
            view="login",
            error="Firebase database is not configured."
        )

    user_email = session["user_email"]
    existing_forms = {}

    docs = db.collection("forms").where("user_id", "==", user_email).stream()

    for doc in docs:
        data = doc.to_dict()
        responses = (
            db.collection("forms")
            .document(doc.id)
            .collection("responses")
            .stream()
        )
        data["count"] = sum(1 for _ in responses)
        existing_forms[doc.id] = data

    return render_template(
        "admin.html",
        view="home",
        existing_forms=existing_forms,
        user_id=user_email
    )


@app.route("/login", methods=["POST"])
def login():
    if not db:
        return render_template(
            "admin.html",
            view="login",
            error="Firebase database is not configured."
        )

    require_csrf()

    email = clean_email(request.form.get("email"))
    password = request.form.get("password", "")

    if not email or not password:
        return render_template(
            "admin.html",
            view="login",
            error="Email and password are required."
        )

    if not is_valid_email(email):
        return render_template(
            "admin.html",
            view="login",
            error="Enter a valid email address."
        )

    user_ref = db.collection("users").document(email)
    user_doc = user_ref.get()

    if user_doc.exists:
        data = user_doc.to_dict()
        stored_hash = data.get("password_hash")
        stored_plain = data.get("password")  # legacy compatibility only

        valid = False
        if stored_hash:
            valid = check_password_hash(stored_hash, password)
        elif stored_plain:
            valid = secrets.compare_digest(str(stored_plain), password)

        if not valid:
            return render_template(
                "admin.html",
                view="login",
                error="Incorrect password."
            )

        # Upgrade old plain-password accounts immediately.
        if not stored_hash:
            user_ref.set({
                "password_hash": generate_password_hash(password),
                "password": firestore.DELETE_FIELD
            }, merge=True)
    else:
        # Keep the original QuickForm behavior: first login/registers account.
        user_ref.set({
            "password_hash": generate_password_hash(password),
            "file_prefix": "QuickForm",
            "created_at": firestore.SERVER_TIMESTAMP
        })

    # Mirror the verified QuickForm password into Firebase Auth so the
    # client-side password-reset flow can use Firebase's email delivery.
    sync_firebase_password(email, password)

    session.clear()
    session["user_email"] = email
    session["_csrf_token"] = secrets.token_urlsafe(32)
    return redirect("/")


@app.route("/logout", methods=["GET", "POST"])
def logout():
    if request.method == "POST":
        require_csrf()
    session.clear()
    return redirect("/")


@app.route("/settings", methods=["GET", "POST"])
@admin_required
def settings_page():
    user_email = session["user_email"]
    user_ref = db.collection("users").document(user_email)

    if request.method == "POST":
        require_csrf()

        new_prefix = request.form.get("file_prefix", "QuickForm").strip()
        new_prefix = re.sub(r"[^A-Za-z0-9 _.-]", "", new_prefix)[:80] or "QuickForm"

        user_ref.set({"file_prefix": new_prefix}, merge=True)
        return redirect("/settings")

    user_doc = user_ref.get()
    data = user_doc.to_dict() if user_doc.exists else {}

    return render_template(
        "admin.html",
        view="settings",
        user_id=user_email,
        prefix=data.get("file_prefix", "QuickForm")
    )


@app.route("/create-page")
@admin_required
def create_page():
    return render_template("admin.html", view="create", is_edit=False)


@app.route("/edit/<form_id>")
@admin_required
def edit_form(form_id):
    doc = get_owned_form(form_id)
    if not doc:
        return redirect("/")

    data = doc.to_dict()

    return render_template(
        "admin.html",
        view="edit",
        is_edit=True,
        form_id=form_id,
        edit_title=data.get("title", ""),
        edit_fields=data.get("fields", []),
        edit_description=data.get("description", ""),
        edit_unique_identifier=data.get("unique_identifier", "")
    )


@app.route("/create-form", methods=["POST"])
@admin_required
def create_form():
    require_csrf()

    title = request.form.get("form_title", "QuickForm").strip()
    clean_title = re.sub(r"[^\w\s-]+", "", title).strip()[:100] or "QuickForm"

    description = re.sub(r"\s+", " ", request.form.get("description", "").strip())[:500]

    raw_fields = request.form.getlist("custom_fields[]")
    clean_fields = []
    seen = set()

    for raw in raw_fields:
        field = re.sub(r"\s+", " ", raw.strip())[:100]
        if field and field.casefold() not in seen:
            clean_fields.append(field)
            seen.add(field.casefold())

    if not clean_fields:
        return render_template(
            "admin.html",
            view="create",
            is_edit=False,
            error="Add at least one field."
        )

    unique_identifier = re.sub(r"\s+", " ", request.form.get("unique_identifier", "").strip())[:100]
    if unique_identifier and unique_identifier.casefold() not in {f.casefold() for f in clean_fields}:
        unique_identifier = clean_fields[0] if clean_fields else ""
    if not unique_identifier and clean_fields:
        unique_identifier = clean_fields[0]

    existing_form_id = request.form.get("existing_form_id", "").strip()

    if existing_form_id:
        # Edit is only allowed on the logged-in user's own form.
        form_doc = get_owned_form(existing_form_id)
        if not form_doc:
            return redirect("/")

        form_id = existing_form_id
    else:
        form_id = f"{re.sub(r'[^A-Za-z0-9_-]+', '', clean_title) or 'QuickForm'}_{secrets.token_hex(3)}"

    db.collection("forms").document(form_id).set({
        "title": clean_title,
        "fields": clean_fields,
        "description": description,
        "unique_identifier": unique_identifier,
        "user_id": session["user_email"],
        "status": "active"
    }, merge=True)

    form_url = f"{request.host_url.rstrip('/')}/form/{form_id}"
    wa_share_url = (
        "https://api.whatsapp.com/send?text="
        + "Please%20fill%20this%20form:%20"
        + form_url.replace(":", "%3A").replace("/", "%2F")
    )

    return render_template(
        "admin.html",
        view="success",
        clean_title=clean_title,
        form_url=form_url,
        wa_share_url=wa_share_url
    )


@app.route("/view-data/<form_id>")
@admin_required
def view_data(form_id):
    doc = get_owned_form(form_id)
    if not doc:
        return redirect("/")

    data = doc.to_dict()
    responses = [
        r.to_dict()
        for r in (
            db.collection("forms")
            .document(form_id)
            .collection("responses")
            .stream()
        )
    ]

    return render_template(
        "admin.html",
        view="view_data",
        form_title=data.get("title", form_id),
        fields=data.get("fields", []),
        responses=responses,
        form_id=form_id,
        excel_letters=[chr(65+i) for i in range(26)] + ["A"+chr(65+i) for i in range(26)] + ["B"+chr(65+i) for i in range(26)]
    )


@app.route("/download-excel/<form_id>")
@admin_required
def download_excel(form_id):
    form_doc = get_owned_form(form_id)
    if not form_doc:
        return redirect("/")

    user_doc = db.collection("users").document(session["user_email"]).get()
    user_data = user_doc.to_dict() if user_doc.exists else {}
    prefix = user_data.get("file_prefix", "QuickForm")

    form_data = form_doc.to_dict()
    title = form_data.get("title", form_id)

    data = [
        doc.to_dict()
        for doc in (
            db.collection("forms")
            .document(form_id)
            .collection("responses")
            .stream()
        )
    ]

    if not data:
        return "<script>alert('No data submitted yet!'); window.location.href='/';</script>"

    fields = form_data.get("fields", [])

    # Keep form field order in Excel, then append any legacy/orphan keys.
    ordered_columns = list(fields)
    extra_columns = []
    for row in data:
        for key in row.keys():
            if key not in ordered_columns and key not in extra_columns:
                extra_columns.append(key)

    df = pd.DataFrame(data, columns=ordered_columns + extra_columns)
    for column in df.columns:
        if (
            str(column).strip().casefold() == "name"
            or "full name" in str(column).strip().casefold()
            or "student name" in str(column).strip().casefold()
            or str(column).strip().casefold().endswith(" name")
            or str(column).strip().casefold().startswith("name ")
        ):
            df[column] = df[column].fillna("").map(lambda v: str(v).title() if str(v).strip() else "")

    safe_prefix = re.sub(r"[^A-Za-z0-9 _.-]", "", prefix).strip() or "QuickForm"
    safe_title = re.sub(r"[^A-Za-z0-9 _.-]", "", title).strip() or "Form"

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Responses")

    output.seek(0)

    return send_file(
        output,
        download_name=f"{safe_prefix}_{safe_title}.xlsx",
        as_attachment=True,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


@app.route("/toggle-status/<form_id>", methods=["POST"])
@admin_required
def toggle_status(form_id):
    require_csrf()

    doc = get_owned_form(form_id)
    if not doc:
        return redirect("/")

    data = doc.to_dict()
    current = data.get("status", "active")
    new_status = "closed" if current == "active" else "active"

    doc.reference.update({"status": new_status})
    return redirect("/")


@app.route("/delete-form/<form_id>", methods=["POST"])
@admin_required
def delete_form(form_id):
    require_csrf()

    doc = get_owned_form(form_id)
    if not doc:
        return redirect("/")

    # Delete responses too. Firestore does not automatically delete
    # subcollections when a parent document is deleted.
    responses_ref = (
        db.collection("forms")
        .document(form_id)
        .collection("responses")
    )

    for response in responses_ref.stream():
        response.reference.delete()

    doc.reference.delete()
    return redirect("/")


# ---------------------------------------------------------------------------
# Public form / existing-data lookup
# ---------------------------------------------------------------------------

@app.route("/verify-data/<form_id>", methods=["POST"])
def verify_data(form_id):
    """Verify an identifier without ever returning stored response values.

    The client receives only which fields are missing and whether a second
    identifier is required. Existing private values never leave Firestore.
    """
    if not db:
        return jsonify({"found": False}), 500

    form_doc = db.collection("forms").document(form_id).get()
    if not form_doc.exists:
        return jsonify({"found": False}), 404

    form_data = form_doc.to_dict()
    if form_data.get("status") == "closed":
        return jsonify({"found": False, "closed": True}), 403

    fields = list(form_data.get("fields", []))
    primary = get_primary_identifier(fields, form_id)
    if not primary:
        return jsonify({"found": False})

    payload = request.get_json(silent=True) or {}
    primary_value = str(payload.get("primary", "")).strip()
    secondary_value = str(payload.get("secondary", "")).strip()
    if not primary_value:
        return jsonify({"found": False})

    secondary_field = next((field for field in fields if field != primary), None)

    # First pass: count primary matches without exposing their contents.
    normalized_primary = normalize_value(primary_value)
    primary_matches = []
    responses = (
        db.collection("forms").document(form_id)
        .collection("responses").stream()
    )
    for response in responses:
        data = response.to_dict()
        if normalize_value(data.get(primary)) == normalized_primary:
            primary_matches.append(response)

    if len(primary_matches) > 1 and not secondary_value:
        return jsonify({
            "found": False,
            "needs_secondary": True,
            "missing_fields": [primary, secondary_field] if secondary_field else [primary]
        })

    match_doc = None
    if secondary_value and secondary_field:
        normalized_secondary = normalize_value(secondary_value)
        for response in primary_matches:
            data = response.to_dict()
            if normalize_value(data.get(secondary_field)) == normalized_secondary:
                match_doc = response
                break
    elif len(primary_matches) == 1:
        match_doc = primary_matches[0]

    if not match_doc:
        return jsonify({"found": False})

    # IMPORTANT: only return field names that need user input. Never return
    # stored values, response objects, document IDs, or hidden form values.
    stored = match_doc.to_dict()
    missing_fields = [
        field for field in fields
        if not str(stored.get(field, "") or "").strip()
    ]

    return jsonify({
        "found": True,
        "missing_fields": missing_fields
    })


@app.route("/form/<form_id>", methods=["GET", "POST"])
def student_form(form_id):
    if not db:
        return "Database Error", 500

    form_doc = db.collection("forms").document(form_id).get()
    if not form_doc.exists:
        return "Form Not Found", 404

    form_data = form_doc.to_dict()

    if form_data.get("status") == "closed":
        return (
            "<h2 style='text-align:center;color:#dc2626;margin-top:50px;'>"
            "Submissions Closed for this Form</h2>"
        ), 403

    original_fields = list(form_data.get("fields", []))
    primary_id = get_primary_identifier(original_fields, form_id)
    display_fields = list(original_fields)
    if primary_id in display_fields:
        display_fields.remove(primary_id)
        display_fields.insert(0, primary_id)

    if request.method == "POST":
        submission = {}
        for index, field in enumerate(display_fields):
            value = request.form.get(f"field_{index}", "").strip()
            if "email" in field.lower() and value:
                value = clean_email(value)
                if not is_valid_email(value):
                    return (
                        "<h2 style='text-align:center;color:#dc2626;margin-top:50px;'>"
                        "Invalid Email Address.</h2>"
                    ), 400
            submission[field] = value

        if not display_fields:
            return "This form has no fields.", 400

        primary_value = submission.get(primary_id, "").strip()
        if not primary_value:
            return "Primary field is required.", 400

        secondary_field = next((f for f in display_fields if f != primary_id), None)
        secondary_value = submission.get(secondary_field, "").strip() if secondary_field else ""

        match_doc = find_response_by_identifier(
            form_id, primary_id, primary_value, secondary_field, secondary_value or None
        )

        responses_collection = (
            db.collection("forms").document(form_id).collection("responses")
        )

        if match_doc:
            update_submission = {key: value for key, value in submission.items() if str(value).strip()}
            match_doc.reference.set(update_submission, merge=True)
        else:
            responses_collection.add(submission)

        return render_template(
            "index.html", success=True, form_title=form_data.get("title"),
            description=form_data.get("description", ""), fields=display_fields,
            original_fields=original_fields, form_id=form_id, done=False
        )

    done = request.args.get("done") == "1"
    return render_template(
        "index.html", success=False, form_title=form_data.get("title"),
        description=form_data.get("description", ""), fields=display_fields,
        original_fields=original_fields, form_id=form_id, done=done
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
