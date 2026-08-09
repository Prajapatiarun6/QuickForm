import io
import json
import os
import re
import secrets
from flask import Flask, render_template, request, redirect, jsonify, send_file, session
import firebase_admin
from firebase_admin import credentials, firestore
import pandas as pd

app = Flask(__name__)
app.secret_key = secrets.token_hex(16)

# Firebase Setup
if not firebase_admin._apps:
    if os.path.exists("firebase_key.json"):
        cred = credentials.Certificate("firebase_key.json")
        firebase_admin.initialize_app(cred)
    else:
        cred_json = json.loads(os.environ.get("FIREBASE_CONFIG_JSON", "{}"))
        if cred_json:
            cred = credentials.Certificate(cred_json)
            firebase_admin.initialize_app(cred)

db = firestore.client() if firebase_admin._apps else None


def clean_username(val):
    return re.sub(r"\s+", "", str(val or "").strip())


def get_existing_db_fields(form_id):
    """Firestore से केवल वही फ़ील्ड्स ढूँढता है जो पुराने डेटाबेस में पहले से भरे जा चुके हैं"""
    existing_keys = set()
    if db and form_id:
        resps = db.collection("forms").document(form_id).collection("responses").limit(10).stream()
        for r in resps:
            existing_keys.update(r.to_dict().keys())
    return existing_keys


def get_primary_identifier(fields, form_id=None):
    existing_keys = get_existing_db_fields(form_id)
    
    # अगर पुराने डेटाबेस में रिकॉर्ड्स मौजूद हैं, तो यूनिक आईडी सिर्फ पुराने फ़ील्ड्स में से ही चुनी जाएगी!
    candidate_fields = [f for f in fields if f in existing_keys] if existing_keys else fields

    for key in ["phone", "mobile", "email", "enroll", "roll", "id", "name"]:
        for f in candidate_fields:
            if key in f.lower():
                return f
    return candidate_fields[0] if candidate_fields else (fields[0] if fields else None)


@app.route("/")
def admin_panel():
    if "user_id" not in session:
        return render_template("admin.html", view="login")

    user_id = session["user_id"]
    existing_forms = {}

    if db:
        docs = db.collection("forms").where("user_id", "==", user_id).stream()
        for doc in docs:
            fdata = doc.to_dict()
            resps = db.collection("forms").document(doc.id).collection("responses").stream()
            fdata["count"] = len(list(resps))
            existing_forms[doc.id] = fdata

    return render_template("admin.html", view="home", existing_forms=existing_forms, user_id=user_id)


@app.route("/login", methods=["POST"])
def login():
    user_id = clean_username(request.form.get("user_id"))
    password = request.form.get("password", "").strip()

    if not user_id or not password or not db:
        return render_template("admin.html", view="login", error="User ID & Password Required!")

    user_ref = db.collection("users").document(user_id).get()

    if user_ref.exists:
        if user_ref.to_dict().get("password") == password:
            session["user_id"] = user_id
            return redirect("/")
        else:
            return render_template("admin.html", view="login", error="Wrong Password!")
    else:
        db.collection("users").document(user_id).set({"password": password, "file_prefix": "QuickForm"})
        session["user_id"] = user_id
        return redirect("/")


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")


@app.route("/settings", methods=["GET", "POST"])
def settings_page():
    if "user_id" not in session or not db:
        return redirect("/")

    user_id = session["user_id"]
    user_ref = db.collection("users").document(user_id)

    if request.method == "POST":
        new_prefix = request.form.get("file_prefix", "QuickForm").strip()
        user_ref.update({"file_prefix": new_prefix})
        return redirect("/settings")

    user_doc = user_ref.get()
    pwd = user_doc.to_dict().get("password", "******") if user_doc.exists else "******"
    prefix = user_doc.to_dict().get("file_prefix", "QuickForm") if user_doc.exists else "QuickForm"

    return render_template("admin.html", view="settings", user_id=user_id, password=pwd, prefix=prefix)


@app.route("/create-page")
def create_page():
    if "user_id" not in session:
        return redirect("/")
    return render_template("admin.html", view="create", is_edit=False)


@app.route("/edit/<form_id>")
def edit_form(form_id):
    if "user_id" not in session or not db:
        return redirect("/")

    doc = db.collection("forms").document(form_id).get()
    if doc.exists and doc.to_dict().get("user_id") == session["user_id"]:
        data = doc.to_dict()
        return render_template(
            "admin.html",
            view="edit",
            is_edit=True,
            form_id=form_id,
            edit_title=data.get("title", ""),
            edit_fields=data.get("fields", [])
        )
    return redirect("/")


@app.route("/create-form", methods=["POST"])
def create_form():
    if "user_id" not in session:
        return redirect("/")

    title = request.form.get("form_title", "QuickForm").strip()
    clean_title = re.sub(r"\W+", "", title) or "QuickForm"
    user_id = session["user_id"]

    existing_form_id = request.form.get("existing_form_id", "").strip()
    form_id = existing_form_id if existing_form_id else f"{clean_title}_{secrets.token_hex(2)}"

    raw_fields = request.form.getlist("custom_fields[]")
    clean_fields = [f.strip() for f in raw_fields if f.strip() != ""]

    if db:
        db.collection("forms").document(form_id).set({
            "title": clean_title,
            "fields": clean_fields,
            "user_id": user_id,
            "status": "active"
        }, merge=True)

    form_url = f"{request.host_url}form/{form_id}"
    wa_share_url = f"https://api.whatsapp.com/send?text=Please%20fill%20this%20form:%20{form_url}"

    return render_template(
        "admin.html",
        view="success",
        clean_title=clean_title,
        form_url=form_url,
        wa_share_url=wa_share_url
    )


@app.route("/view-data/<form_id>")
def view_data(form_id):
    if "user_id" not in session or not db:
        return redirect("/")

    doc = db.collection("forms").document(form_id).get()
    if not doc.exists or doc.to_dict().get("user_id") != session["user_id"]:
        return redirect("/")

    fdata = doc.to_dict()
    responses_ref = db.collection("forms").document(form_id).collection("responses").stream()
    responses = [r.to_dict() for r in responses_ref]

    return render_template(
        "admin.html",
        view="view_data",
        form_title=fdata.get("title"),
        fields=fdata.get("fields", []),
        responses=responses,
        form_id=form_id
    )


@app.route("/download-excel/<form_id>")
def download_excel(form_id):
    if "user_id" not in session or not db:
        return redirect("/")

    user_doc = db.collection("users").document(session["user_id"]).get()
    prefix = user_doc.to_dict().get("file_prefix", "QuickForm") if user_doc.exists else "QuickForm"

    form_doc = db.collection("forms").document(form_id).get()
    title = form_doc.to_dict().get("title", form_id) if form_doc.exists else form_id

    responses_ref = db.collection("forms").document(form_id).collection("responses").stream()
    data = [doc.to_dict() for doc in responses_ref]

    if not data:
        return "<script>alert('No data submitted yet!'); window.location.href='/';</script>"

    df = pd.DataFrame(data)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Responses")

    output.seek(0)
    return send_file(output, download_name=f"{prefix}_{title}.xlsx", as_attachment=True)


@app.route("/toggle-status/<form_id>", methods=["POST"])
def toggle_status(form_id):
    if "user_id" not in session or not db:
        return redirect("/")

    doc_ref = db.collection("forms").document(form_id)
    doc = doc_ref.get()
    if doc.exists and doc.to_dict().get("user_id") == session["user_id"]:
        curr_status = doc.to_dict().get("status", "active")
        new_status = "closed" if curr_status == "active" else "active"
        doc_ref.update({"status": new_status})

    return redirect("/")


@app.route("/delete-form/<form_id>", methods=["POST"])
def delete_form(form_id):
    if "user_id" not in session or not db:
        return redirect("/")

    doc = db.collection("forms").document(form_id).get()
    if doc.exists and doc.to_dict().get("user_id") == session["user_id"]:
        db.collection("forms").document(form_id).delete()

    return redirect("/")


@app.route("/verify-data/<form_id>", methods=["POST"])
def verify_data(form_id):
    if not db:
        return jsonify({"found": False})

    req_data = request.json or {}
    p_val = req_data.get("primary", "").strip().lower()
    s_val = req_data.get("secondary", "").strip().lower()

    if not p_val:
        return jsonify({"found": False})

    responses_ref = db.collection("forms").document(form_id).collection("responses").stream()
    all_resps = [r.to_dict() for r in responses_ref]

    p_matches = []
    for r in all_resps:
        vals = [str(v).strip().lower() for v in r.values()]
        if p_val in vals:
            p_matches.append(r)

    if not p_matches:
        return jsonify({"found": False})

    if len(p_matches) == 1 and not s_val:
        return jsonify({"found": True, "data": p_matches[0]})

    if s_val:
        s_matches = []
        for r in p_matches:
            vals = [str(v).strip().lower() for v in r.values()]
            if s_val in vals:
                s_matches.append(r)

        if len(s_matches) >= 1:
            return jsonify({"found": True, "data": s_matches[0]})

    return jsonify({"found": False, "needs_secondary": True})


@app.route("/form/<form_id>", methods=["GET", "POST"])
def student_form(form_id):
    if not db:
        return "Database Error", 500

    form_doc = db.collection("forms").document(form_id).get()
    if not form_doc.exists:
        return "❌ Form Not Found", 404

    form_data = form_doc.to_dict()
    if form_data.get("status") == "closed":
        return "<h2 style='text-align: center; color: #dc2626; margin-top: 50px;'>🔒 Submissions Closed for this Form</h2>", 403

    fields = list(form_data.get("fields", []))
    
    # पुराना भरा हुआ फ़ील्ड ही टॉप (Index 0) बनेगा, नया फ़ील्ड नहीं!
    primary_id = get_primary_identifier(fields, form_id)
    if primary_id and primary_id in fields:
        fields.remove(primary_id)
        fields.insert(0, primary_id)

    if request.method == "POST":
        submission = {}
        for idx, field in enumerate(fields):
            val = request.form.get(f"field_{idx}", "").strip()
            
            # STRICT EMAIL VALIDATION
            if "email" in field.lower() and val:
                val = val.lower()
                if not re.match(r"^[^@]+@[^@]+\.[^@]+$", val):
                    return "<h2 style='text-align: center; color: #dc2626; margin-top: 50px;'>❌ Invalid Email Address! Must contain '@' and domain (e.g., name@gmail.com)</h2>", 400

            if "name" in field.lower():
                val = val.title()
            submission[field] = val

        first_val = submission.get(fields[0], "").strip().lower() if fields else ""
        responses_ref = db.collection("forms").document(form_id).collection("responses").stream()
        
        match_doc = None
        for rdoc in responses_ref:
            rdata = rdoc.to_dict()
            vals = [str(v).strip().lower() for v in rdata.values()]
            if first_val and first_val in vals:
                match_doc = rdoc
                break

        if match_doc:
            match_doc.reference.set(submission, merge=True)
        else:
            db.collection("forms").document(form_id).collection("responses").add(submission)

        return """
        <div style="font-family: sans-serif; text-align: center; padding: 40px; max-width: 450px; margin: auto;">
            <h2 style="color: #16a34a;">✅ Response Saved Successfully!</h2>
            <p style="color: #64748b; margin-top: 10px;">Your response has been recorded.</p>
        </div>
        """

    return render_template("index.html", form_title=form_data.get("title"), fields=fields, form_id=form_id)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)