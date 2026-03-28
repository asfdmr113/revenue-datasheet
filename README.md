# Warung cashflow tracker

A small Streamlit app for logging daily sales and expenses, syncing to PostgreSQL (Supabase) and Google Sheets, with an owner dashboard for monthly monitoring.

## Prerequisites

- **Python 3.10+** (3.10 or newer recommended)
- A **Supabase** project (database + optional API keys for the Python client)
- A **Google Cloud** service account with access to a backup spreadsheet (optional but supported for dual save)

## 1. Clone and enter the project

```bash
git clone <your-repo-url>
cd revenue-datasheet
```

## 2. Create a virtual environment (recommended)

**Windows (PowerShell):**

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

**macOS / Linux:**

```bash
python3 -m venv .venv
source .venv/bin/activate
```

## 3. Install dependencies

```bash
pip install -r requirements.txt
```

## 4. Database setup (Supabase)

In the Supabase dashboard, open **SQL** → **New query**, then run:

- [`db_setup.sql`](db_setup.sql) for a minimal `transactions` table, **or**
- [`SQL/supabase-database.sql`](SQL/supabase-database.sql) for `transactions`, RLS policies, and the **`monthly_summary`** view used by the Dashboard tab.

Use the connection details from **Project Settings → Database** for the `[postgres]` block in secrets (host, user, password, port, database).

### Streamlit Community Cloud

In the app’s **Settings → Secrets**, use a **`[postgres]`** table with the same keys as below. The app accepts **`user`** or **`username`**, and optionally a single **`database_url`** (full `postgresql://…` string from Supabase **Connection string** / URI mode) instead of separate host/user/password.

**Supabase:** the database user is almost always **`postgres`**. The password is the **database password** from **Project Settings → Database** (not the `anon` / `service_role` API keys).

## 5. Configure secrets

Create a folder **`.streamlit`** in the project root (if it does not exist) and add **`secrets.toml`**. This file is listed in [`.gitignore`](.gitignore) and must not be committed.

Minimal structure (fill in your real values):

```toml
[postgres]
host = "db.YOUR_PROJECT.supabase.co"
port = 5432
database = "postgres"
user = "postgres"
password = "YOUR_DB_PASSWORD"

# Or one line (Session mode URI from Supabase is fine; app adds psycopg2 driver):
# database_url = "postgresql://postgres:YOUR_PASSWORD@db.YOUR_PROJECT.supabase.co:5432/postgres"

[connections.gsheets]
spreadsheet = "https://docs.google.com/spreadsheets/d/YOUR_ID/edit"
worksheet = "Transactions"
type = "service_account"
project_id = "..."
private_key_id = "..."
private_key = "-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----\n"
client_email = "...@....iam.gserviceaccount.com"
client_id = "..."
auth_uri = "https://accounts.google.com/o/oauth2/auth"
token_uri = "https://oauth2.googleapis.com/token"
auth_provider_x509_cert_url = "https://www.googleapis.com/oauth2/v1/certs"
client_x509_cert_url = "..."

dashboard_password = "choose-a-strong-password"

[supabase]
url = "https://YOUR_PROJECT.supabase.co"
key = "your-anon-or-service-role-key"
```

Share the Google Sheet with the service account **client email** (Editor).

For more detail, see the module docstring at the top of [`app.py`](app.py).

## 6. Launch the app

From the project root, with your virtual environment activated:

```bash
streamlit run app.py
```

Streamlit will print a local URL (usually [http://localhost:8501](http://localhost:8501)). Open it in your browser.

- **Catat** — enter transactions and quick expenses.
- **Dashboard** — enter the owner password from `dashboard_password`, then review metrics (including **`monthly_summary`** when Supabase is configured) and the daily profit chart.

To stop the server, press **Ctrl+C** in the terminal.

## Troubleshooting

- **Missing secrets:** The app expects `.streamlit/secrets.toml`. Without `[postgres]` or `[connections.gsheets]`, saving or charts may fail with clear messages in the UI.
- **Port in use:** Run on another port, for example:  
  `streamlit run app.py --server.port 8502`
- **Supabase API errors:** Ensure `[supabase]` `url` and `key` match the Supabase project and that RLS policies allow the operations you need for your key type ([Supabase Row Level Security](https://supabase.com/docs/guides/auth/row-level-security)).

## License

See [`LICENSE`](LICENSE).
