# VidStore

A private, modern photo & video gallery web application built with **FastAPI**, **Jinja2**, and **Supabase**.

---

## Architecture & Features

- **Backend:** FastAPI + Uvicorn + Jinja2 Templates.
- **Database:** SQLAlchemy supporting **PostgreSQL** (e.g. Supabase Postgres) and **SQLite** (local development).
- **Storage:** Dual-mode storage:
  - **Cloud:** Private Supabase Storage bucket (`gallery`) with high-speed CDN and signed streaming URLs.
  - **Local:** Local disk storage in `uploads/{user_id}/` with HTTP Range request support.
- **Security:**
  - Password hashing using `bcrypt`.
  - Signed `HttpOnly` session cookies (`itsdangerous`) with `Secure` flag on HTTPS / Vercel.
  - Security headers (`X-Content-Type-Options: nosniff`, `X-Frame-Options`, `Referrer-Policy`).
  - Owner-restricted media access.

---

## 💻 Local Development

### 1. Install dependencies
```bash
cd "VidStore"
pip install -r requirements.txt
```

### 2. Run the application
```bash
uvicorn main:app --reload
```
Open **http://127.0.0.1:8000** in your browser.

---

## 🚀 How to Deploy on Vercel

VidStore is pre-configured for deployment as a Vercel Serverless Function via [`vercel.json`](file:///c:/Data%20Things/VidStore/vercel.json) and [`api/index.py`](file:///c:/Data%20Things/VidStore/api/index.py).

### Prerequisites
1. A [Vercel account](https://vercel.com).
2. A [Supabase account & project](https://supabase.com).
   - Bucket: Ensure a storage bucket named `gallery` exists in **Storage** (Private or Public).
   - Database: Retrieve your Supabase PostgreSQL connection URI from **Project Settings → Database → Connection String (URI)**.

---

### Method A: Deploy via GitHub & Vercel Dashboard (Recommended)

1. **Initialize Git and commit the project:**
   ```bash
   cd "VidStore"
   git init
   git add .
   git commit -m "Initial commit for VidStore"
   ```
   *(Note: `.gitignore` automatically prevents your `Keys.env`, local database, and uploaded files from being committed.)*

2. **Push to a private GitHub repository:**
   ```bash
   git remote add origin https://github.com/<your-username>/vidstore.git
   git branch -M main
   git push -u origin main
   ```

3. **Import into Vercel:**
   - Go to [vercel.com/new](https://vercel.com/new).
   - Select your `vidstore` repository and click **Import**.
   - Under **Framework Preset**, leave as **Other**.
   - Under **Environment Variables**, add the following:

     | Key | Description | Example / Source |
     |---|---|---|
     | `VIDSTORE_SECRET` | Session signing key (at least 32 characters) | Run `python -c "import secrets; print(secrets.token_hex(32))"` |
     | `SUPABASE_URL` | Your Supabase project URL | `https://eqvvdwdaxkqyjncxruys.supabase.co` |
     | `SUPABASE_SERVICE_ROLE_KEY` | Supabase service role secret | From Supabase Project Settings → API |
     | `SUPABASE_BUCKET` | Storage bucket name | `gallery` |
     | `DATABASE_URL` | Supabase PostgreSQL URI | `postgresql://postgres.[ref]:[PASSWORD]@aws-0-[region].pooler.supabase.com:6543/postgres` |

4. **Click Deploy:**
   Vercel will build the Python environment and provide your live URL (e.g. `https://vidstore-xxx.vercel.app`).

---

### Method B: Deploy via Vercel CLI

1. **Install Vercel CLI (if not installed):**
   ```bash
   npm install -g vercel
   ```

2. **Deploy from project folder:**
   ```bash
   cd "VidStore"
   vercel
   ```

3. **Add Environment Variables to Vercel:**
   ```bash
   vercel env add VIDSTORE_SECRET
   vercel env add SUPABASE_URL
   vercel env add SUPABASE_SERVICE_ROLE_KEY
   vercel env add SUPABASE_BUCKET
   vercel env add DATABASE_URL
   ```

4. **Deploy to production:**
   ```bash
   vercel --prod
   ```

---

## 📁 Project Structure

```text
VidStore/
├── api/
│   └── index.py         # Vercel Serverless Function entrypoint
├── main.py              # FastAPI app, routes, security headers & upload handling
├── database.py          # SQLAlchemy models (PostgreSQL & SQLite)
├── auth.py              # Direct bcrypt hashing & signed cookie helpers
├── storage.py           # Supabase Cloud Storage + local disk fallback
├── templates/
│   ├── login.html       # Tabbed login & register template
│   └── gallery.html     # Responsive media grid & full-screen lightbox
├── static/
│   └── style.css        # Responsive dark theme UI
├── vercel.json          # Vercel serverless routing config
├── .gitignore           # Prevents secrets, DB & uploads from being tracked
├── requirements.txt     # Python dependencies
└── README.md            # Documentation & deployment guide
```
