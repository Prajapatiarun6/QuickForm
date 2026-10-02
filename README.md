# QuickForm — refined presentation build

This build preserves the existing QuickForm Flask + Firebase/Firestore workflow and adds a restrained product/UI refinement.

## Preserved
- Firebase Admin / Firestore data storage
- Admin login session flow
- Google sign-in bridge through Firebase Auth
- Password reset through Firebase Auth
- Form create/edit
- Dynamic fields and Enter-to-add-field behavior
- Existing-record verification and same-record merge/upsert
- Excel export
- View Data
- Close/re-open and delete form
- Public form sharing, WhatsApp, copy link and QR code
- Submission success modal

## Important privacy behavior
The public `/verify-data/<form_id>` endpoint does **not** return stored response values. For an existing record it returns only `missing_fields` (and, when necessary, `needs_secondary`). The browser therefore cannot receive or briefly render old private answers.

## Logo
`static/quickform-logo.svg` is the new QuickForm mark and is used as the favicon and product mark.

## Run locally
```bash
source venv/bin/activate
python -m pip install -r requirements.txt
export SECRET_KEY="$(python -c 'import secrets; print(secrets.token_hex(32))')"
python app.py
```

Open `http://localhost:5000`.

Keep your existing `firebase_key.json` out of source control and do not replace it with a generated file from this package.
