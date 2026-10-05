# ⚡ QuickForm

<div align="center">
  <img src="static/quickform-logo.svg" alt="QuickForm Logo" width="85" height="85" />
  <h3>Dynamic Digital Form Creation & Response Management System</h3>
  <p>Eliminate redundant data entry, avoid duplicate records, and manage responses with modern cloud integration.</p>

  [![Deployment](https://img.shields.io/badge/Deployed%20on-Vercel-black?style=flat&logo=vercel)](https://quick-form-alpha.vercel.app/)
  [![Framework](https://img.shields.io/badge/Backend-Flask%203.x-blue?style=flat&logo=flask)](https://flask.palletsprojects.com/)
  [![Database](https://img.shields.io/badge/Database-Firebase%20Firestore-orange?style=flat&logo=firebase)](https://firebase.google.com/)
  [![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
</div>

---

## 📌 Executive Summary

**QuickForm** is a dynamic web-based form creation and response lifecycle management platform engineered to resolve the key limitations of traditional and first-generation digital forms.

### The Problem
Traditional data collection often requires manual paper work or repetitive electronic entries. When administrators expand a form by appending extra fields later, respondents are typically forced to re-fill the entire form from scratch. This introduces data fragmentation, duplicate database entries, and human error.

### The Solution
QuickForm solves this by combining dynamic field generation with **Unique Identifier-based record recognition** and **Differential Merging (Upsert)**. When a respondent enters their identifier (such as Phone Number or Student ID), QuickForm verifies the record, checks which fields are missing, prompts *only* for the newly required values, and merges them directly into the existing Firestore record without creating duplicate entries.

---

## 🚀 Key Architectural Features

- **Dynamic Form Builder**: Instant form generation with custom fields, reordering, and designated primary identifier configuration.
- **Differential Upsert Engine**: Seamlessly updates existing student/client responses using Firestore merge operations rather than creating new redundant records.
- **Zero-Exposure Data Verification (`/verify-data`)**: Security-first verification endpoint that evaluates identifier matching on the server side and returns only missing field names. Stored answers or personally identifiable information (PII) are **never** leaked to the client browser.
- **Omnichannel Distribution**: Automatic QR code synthesis, one-click WhatsApp sharing, and native clipboard/Web Share API integration.
- **Spreadsheet Analytics & Export**: In-browser Excel grid interface with dynamic `.xlsx` document generation powered by `pandas` and `openpyxl`.
- **Dual Authentication Bridge**: Native secure password hashing (`generate_password_hash` / `check_password_hash`) mirrored to Firebase Auth for Google OAuth and automated password reset workflows.
- **Serverless Session Resilience**: Deterministic session secret derivation based on service account secrets to preserve authenticated admin sessions across stateless serverless restarts (Vercel).

---

## 🏗️ System Workflow & Logic Flow

### 1. High-Level System Workflow

```text
Admin Login / Register ──► Create / Edit Form ──► Define Custom Fields & Unique Key
                                                              │
                                                              ▼
Export to Excel ◄── Live Response Grid ◄── Cloud Firestore ◄── Public Form (Link / QR / WhatsApp)
```

### 2. Differential Verification & Upsert Pipeline

```text
               Respondent enters Primary Identifier
                                 │
                                 ▼
                     POST /verify-data/<form_id>
                                 │
                ┌────────────────┴────────────────┐
                ▼                                 ▼
      [Record NOT Found]                  [Record Matches]
                │                                 │
    Render all form fields              Compare stored fields against
                │                           current form schema
                ▼                                 │
         User completes form                      ▼
                │                       Return ONLY missing_fields
                ▼                       (Old data remains concealed)
    Collection.add(submission)                    │
                                                  ▼
                                         User enters only missing data
                                                  │
                                                  ▼
                                       doc.reference.set(..., merge=True)
```

---

## 🛠️ Tech Stack & Dependencies

| Layer | Technologies |
| :--- | :--- |
| **Backend Framework** | Python 3.10+, Flask, Werkzeug |
| **Database & Cloud Auth**| Google Cloud Firestore, Firebase Authentication, Firebase Admin SDK |
| **Data Processing** | Pandas, OpenPyXL |
| **Frontend Architecture**| Responsive HTML5, Glassmorphism CSS, Vanilla ES6+ JavaScript |
| **Client Libraries** | QRCode.js, Google Firebase JS SDK |
| **Deployment & Hosting**| Vercel Serverless Functions (`@vercel/python`) |

---

## 📂 Project Structure

```text
QuickForm/
├── static/
│   └── quickform-logo.svg      # Unified branding SVG & Favicon
├── templates/
│   ├── admin.html              # Admin workspace: login, dashboard, builder & Excel viewer
│   ├── index.html              # Public form interface with real-time field reconciliation
│   ├── student.html            # Asynchronous validation form interface
│   ├── login.html              # Dedicated client-side authentication portal
│   └── error.html              # Exception & error fallback display
├── app.py                      # Flask core router, session manager, Firestore controllers
├── requirements.txt            # Dependency manifest
├── vercel.json                 # Vercel serverless routing configuration
└── README.md                   # Project documentation
```

---

## ⚙️ Installation & Local Setup

### Prerequisites
- Python 3.10 or higher
- A Firebase project with Firestore and Authentication enabled
- A service account credential file (`firebase_key.json`)

### Step-by-Step Instructions

1. **Clone the repository:**
   ```bash
   git clone https://github.com/your-username/quickform.git
   cd quickform
   ```

2. **Create and activate a virtual environment:**
   ```bash
   python -m venv venv
   source venv/bin/activate       # macOS / Linux
   # On Windows: venv\Scripts\activate
   ```

3. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

4. **Configure Firebase Credentials:**
   Place your Firebase Admin SDK service account JSON file in the project root named as:
   ```text
   firebase_key.json
   ```
   *Alternatively*, you can set the `FIREBASE_CONFIG_JSON` environment variable containing the raw JSON string.

5. **Set local environment variables:**
   ```bash
   export SECRET_KEY="$(python -c 'import secrets; print(secrets.token_hex(32))')"
   ```

6. **Run the local development server:**
   ```bash
   python app.py
   ```
   Access the dashboard at `http://localhost:5000`.

---

## 🔒 Security & Privacy Implementation

- **Data Privacy by Design**: Unlike naive search endpoints, the public verify endpoint does not return matching records to the client. It calculates missing attributes entirely server-side, protecting personal information from being harvested.
- **CSRF Defense**: All modifying actions (`POST`) validate one-time session-bound tokens before execution.
- **Input Sanitization**: Titles, custom field names, and export labels are cleaned and restricted via regex checks to prevent injection attacks and file system corruption.
- **Session Hardening**: Cookies enforce `HTTPOnly`, `SameSite=Lax`, and dynamically scale to `Secure` under production environments.

---

## 🔮 Future Scope

- Detailed response analytics with automated data visualization graphs.
- Multi-tier role-based access control (RBAC) for organizations and educational institutes.
- Automated email and SMS trigger integrations on successful submission.
- Direct webhook sync with Google Sheets and third-party tools.

---

## 👨‍💻 Project Information

- **Developer**: Arun Prajapati
- **Branch**: Computer Science & Engineering (CSE, 2nd Year)
- **Live Deployment**: [QuickForm Production Link](https://quick-form-alpha.vercel.app/)