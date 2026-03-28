from __future__ import annotations

import traceback
import calendar
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from urllib.parse import quote_plus

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from supabase import create_client
from sqlalchemy import create_engine, text
from gspread.exceptions import WorksheetNotFound
from streamlit_gsheets import GSheetsConnection

EXPENSE_CATEGORIES = [
    "Ingredients",
    "Gas/Utilities",
    "Packaging",
    "Others",
]

# Preset warung expenses (tap to save; optional "edit price" first).
QUICK_EXPENSES = {
    "Beras (5kg)": {"price": 80000, "cat": "Ingredients"},
    "Minyak (2L)": {"price": 40000, "cat": "Ingredients"},
    "Telur (1kg)": {"price": 32000, "cat": "Ingredients"},
    "Gas LPG 3kg": {"price": 22000, "cat": "Utilities"},
    "Ayam (1kg)": {"price": 45000, "cat": "Ingredients"},
    "Plastik/Bungkus": {"price": 15000, "cat": "Packaging"},
}

# Must match db_setup.sql / Supabase `transactions` table (same order as columns in sheet).
TRANSACTION_SHEET_COLUMNS = [
    "id",
    "date",
    "type",
    "category",
    "amount",
    "description",
    "created_at",
]


def format_idr(value: float | Decimal | None) -> str:
    """Indonesian Rupiah: Rp 1.234.567 (dot thousands separator)."""
    if value is None:
        return "Rp 0"
    try:
        n = float(value)
        s = f"{n:,.0f}"
        return "Rp " + s.replace(",", ".")
    except (TypeError, ValueError):
        return "Rp 0"


def parse_amount(raw: Any) -> Decimal | None:
    if raw is None:
        return None
    try:
        d = Decimal(str(raw))
        if d < 0:
            return None
        return d.quantize(Decimal("0.01"))
    except Exception:
        return None


def _postgres_secret_get(p, *keys: str):
    """Read nested secrets without raising if a key is missing (Streamlit Cloud / TOML)."""
    for k in keys:
        try:
            v = p[k]
        except Exception:
            continue
        if v is not None and str(v).strip() != "":
            return v
    return None


def get_db_engine():
    p = st.secrets.get("postgres")
    if not p:
        return None, (
            "Konfigurasi [postgres] tidak ditemukan di st.secrets. "
            "Di Streamlit Cloud, tambahkan tabel [postgres] dengan host, user, password, "
            "atau satu baris database_url (lihat README)."
        )
    try:
        raw_url = _postgres_secret_get(
            p, "database_url", "db_url", "url", "connection_string", "uri"
        )
        if raw_url:
            u = str(raw_url).strip()
            if u.startswith("postgresql+psycopg2://"):
                pass
            elif u.startswith("postgresql://"):
                u = "postgresql+psycopg2://" + u[len("postgresql://") :]
            elif u.startswith("postgres://"):
                u = "postgresql+psycopg2://" + u[len("postgres://") :]
            else:
                return None, "[postgres] database_url harus diawali postgresql:// atau postgres://"
            return create_engine(u, pool_pre_ping=True), None

        user = _postgres_secret_get(p, "user", "username", "db_user")
        password = _postgres_secret_get(p, "password", "passwd", "db_password")
        host = _postgres_secret_get(p, "host", "hostname", "db_host")
        port_raw = _postgres_secret_get(p, "port")
        dbn = _postgres_secret_get(p, "database", "dbname", "db") or "postgres"

        if not user or not password or not host:
            return None, (
                "[postgres] tidak lengkap: butuh host, password, dan user "
                "(atau username). Supabase memakai user `postgres`. "
                "Alternatif: satu kunci database_url berisi connection string penuh."
            )

        port = int(port_raw) if port_raw is not None else 5432
        user_q = quote_plus(str(user))
        password_q = quote_plus(str(password))
        url = f"postgresql+psycopg2://{user_q}:{password_q}@{host}:{port}/{dbn}"
        return create_engine(url, pool_pre_ping=True), None
    except Exception as e:
        return None, f"Gagal membuat koneksi database: {e}"


def get_dashboard_password() -> str:
    """Top-level `dashboard_password` or nested `[dashboard] dashboard_password` in secrets.toml."""
    if "dashboard_password" in st.secrets:
        return str(st.secrets["dashboard_password"]).strip()
    nested = st.secrets.get("dashboard")
    if nested is not None and hasattr(nested, "get"):
        v = nested.get("dashboard_password")
        if v is not None:
            return str(v).strip()
    return ""


def get_supabase_client():
    s = st.secrets.get("supabase")
    if not s:
        return None, "Konfigurasi [supabase] (url, key) tidak ditemukan di st.secrets."
    try:
        url = str(s.get("url", "")).strip()
        key = str(s.get("key", "")).strip()
        if not url or not key:
            return None, "[supabase] perlu `url` dan `key`."
        return create_client(url, key), None
    except Exception as e:
        return None, f"Supabase client: {e}"


def _df_series_float(row: pd.Series, *column_names: str) -> float:
    for n in column_names:
        if n in row.index:
            v = row[n]
            if pd.notna(v):
                return float(v)
    return 0.0


def select_monthly_summary_row(df: pd.DataFrame, year: int, month: int) -> pd.Series | None:
    """Pick the summary row for the selected calendar month, or a single-row default."""
    if df is None or df.empty:
        return None

    def col(*names: str) -> str | None:
        lower_map = {str(c).lower(): c for c in df.columns}
        for n in names:
            if n.lower() in lower_map:
                return lower_map[n.lower()]
        return None

    yc = col("year", "yr", "tahun")
    mc = col("month", "m", "bulan")
    if yc and mc:
        try:
            m_series = pd.to_numeric(df[mc], errors="coerce").fillna(0).astype(int)
            y_series = pd.to_numeric(df[yc], errors="coerce").fillna(0).astype(int)
            sub = df[(y_series == year) & (m_series == month)]
            if not sub.empty:
                return sub.iloc[0]
        except Exception:
            pass

    pc = col("period", "month_key", "ym", "year_month", "month_id")
    if pc:
        try:
            prefix = f"{year:04d}-{month:02d}"
            mask = df[pc].astype(str).str.startswith(prefix)
            sub = df[mask]
            if not sub.empty:
                return sub.iloc[0]
        except Exception:
            pass

    # View returns a single current-month row (your one-liner pattern).
    return df.iloc[0]


def gsheets_worksheet_name() -> str:
    conn_cfg = st.secrets.get("connections", {}).get("gsheets", {})
    ws = conn_cfg.get("worksheet")
    if ws:
        return str(ws).strip()
    legacy = st.secrets.get("google", {})
    return str(legacy.get("worksheet_name", "Transactions")).strip() or "Transactions"


def _normalize_sheet_df(df: pd.DataFrame | None) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=TRANSACTION_SHEET_COLUMNS)
    out = df.copy()
    cols = [c for c in out.columns if str(c).strip() and not str(c).startswith("Unnamed")]
    out = out.loc[:, cols] if cols else pd.DataFrame()
    for c in TRANSACTION_SHEET_COLUMNS:
        if c not in out.columns:
            out[c] = ""
    return out[TRANSACTION_SHEET_COLUMNS]


def transaction_row_dataframe(
    tx_date: date,
    tx_type: str,
    category: str,
    amount: Decimal,
    description: str,
    created_at_iso: str,
    db_id: int | None,
) -> pd.DataFrame:
    rid = "" if db_id is None else int(db_id)
    return pd.DataFrame(
        [
            {
                "id": rid,
                "date": tx_date.isoformat(),
                "type": tx_type,
                "category": category,
                "amount": float(amount),
                "description": description,
                "created_at": created_at_iso,
            }
        ],
        columns=TRANSACTION_SHEET_COLUMNS,
    )


def append_transaction_via_gsheets(
    conn: GSheetsConnection,
    worksheet: str,
    new_row: pd.DataFrame,
) -> tuple[bool, str | None]:
    try:
        try:
            existing = conn.read(worksheet=worksheet, ttl=0)
        except WorksheetNotFound:
            conn.create(worksheet=worksheet, data=new_row)
            st.cache_data.clear()
            return True, None
        except Exception as e:
            return False, f"{e}\n{traceback.format_exc()}"

        base = _normalize_sheet_df(existing)
        combined = new_row.copy() if base.empty else pd.concat([base, new_row], ignore_index=True)
        combined = combined[TRANSACTION_SHEET_COLUMNS]
        conn.update(worksheet=worksheet, data=combined)
        st.cache_data.clear()
        return True, None
    except Exception as e:
        return False, f"{e}\n{traceback.format_exc()}"


def save_to_postgres(
    engine,
    tx_date: date,
    tx_type: str,
    category: str | None,
    amount: Decimal,
    description: str | None,
) -> tuple[bool, str | None, int | None]:
    try:
        q = text(
            """
            INSERT INTO transactions (date, type, category, amount, description)
            VALUES (:date, :type, :category, :amount, :description)
            RETURNING id
            """
        )
        with engine.begin() as conn:
            res = conn.execute(
                q,
                {
                    "date": tx_date,
                    "type": tx_type,
                    "category": category if category else None,
                    "amount": float(amount),
                    "description": description or None,
                },
            )
            new_id = res.scalar_one()
        return True, None, int(new_id)
    except Exception as e:
        return False, f"{e}\n{traceback.format_exc()}", None


def save_transaction_dual(
    tx_date: date,
    tx_type: str,
    category: str | None,
    amount: Decimal,
    description: str | None,
    gsheets_conn: GSheetsConnection | None = None,
    gsheets_connect_error: str | None = None,
) -> dict:
    created_at = datetime.utcnow().isoformat() + "Z"
    sheet_cat = category if category else ""
    desc_str = description or ""
    result = {"postgres": False, "sheets": False, "errors": []}
    inserted_id: int | None = None

    engine, db_err = get_db_engine()
    if engine:
        ok, err, inserted_id = save_to_postgres(
            engine, tx_date, tx_type, category, amount, description
        )
        result["postgres"] = ok
        if not ok and err:
            result["errors"].append(("PostgreSQL", err))
    else:
        result["errors"].append(("PostgreSQL", db_err or "Unknown DB error"))

    if gsheets_conn is None:
        msg = gsheets_connect_error or (
            "st.connection('gsheets', type=GSheetsConnection) gagal. "
            "Pastikan [connections.gsheets] di .streamlit/secrets.toml (lihat docstring app.py)."
        )
        result["errors"].append(("Google Sheets", msg))
    else:
        wname = gsheets_worksheet_name()
        row_df = transaction_row_dataframe(
            tx_date,
            tx_type,
            sheet_cat,
            amount,
            desc_str,
            created_at,
            inserted_id if result["postgres"] else None,
        )
        ok, err = append_transaction_via_gsheets(gsheets_conn, wname, row_df)
        result["sheets"] = ok
        if not ok and err:
            result["errors"].append(("Google Sheets", err))

    return result


def normalize_quick_category(cat: str) -> str:
    """Map preset labels to EXPENSE_CATEGORIES (fixes legacy 'Utilities' naming)."""
    if cat == "Utilities":
        return "Gas/Utilities"
    if cat in EXPENSE_CATEGORIES:
        return cat
    return cat


def load_today_profit(engine, d: date) -> float:
    """Net profit for a single calendar day: Sales − Expenses."""
    q = text(
        """
        SELECT
            COALESCE(SUM(CASE WHEN type = 'Sale' THEN amount ELSE 0 END), 0) AS revenue,
            COALESCE(SUM(CASE WHEN type = 'Expense' THEN amount ELSE 0 END), 0) AS expense
        FROM transactions
        WHERE date = :d
        """
    )
    with engine.connect() as conn:
        row = conn.execute(q, {"d": d}).mappings().first()
    if not row:
        return 0.0
    return float(row["revenue"]) - float(row["expense"])


def load_monthly_aggregates(engine, year: int, month: int) -> tuple[pd.DataFrame, dict]:
    start = date(year, month, 1)
    last = calendar.monthrange(year, month)[1]
    end = date(year, month, last)

    totals_q = text(
        """
        SELECT
            COALESCE(SUM(CASE WHEN type = 'Sale' THEN amount ELSE 0 END), 0) AS revenue,
            COALESCE(SUM(CASE WHEN type = 'Expense' THEN amount ELSE 0 END), 0) AS expense
        FROM transactions
        WHERE date >= :start AND date <= :end
        """
    )
    daily_q = text(
        """
        SELECT
            date,
            COALESCE(SUM(CASE WHEN type = 'Sale' THEN amount ELSE 0 END), 0) AS revenue,
            COALESCE(SUM(CASE WHEN type = 'Expense' THEN amount ELSE 0 END), 0) AS expense
        FROM transactions
        WHERE date >= :start AND date <= :end
        GROUP BY date
        ORDER BY date
        """
    )
    with engine.connect() as conn:
        trow = conn.execute(totals_q, {"start": start, "end": end}).mappings().first()
        drows = conn.execute(daily_q, {"start": start, "end": end}).mappings().all()

    revenue = float(trow["revenue"]) if trow else 0.0
    expense = float(trow["expense"]) if trow else 0.0
    totals = {"revenue": revenue, "expense": expense, "net": revenue - expense}

    df = pd.DataFrame([dict(r) for r in drows]) if drows else pd.DataFrame()
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
        df["revenue"] = df["revenue"].astype(float)
        df["expense"] = df["expense"].astype(float)
        df["profit"] = df["revenue"] - df["expense"]
    return df, totals


def ensure_session_defaults():
    if "owner_ok" not in st.session_state:
        st.session_state.owner_ok = False
    if "tx_type_radio" not in st.session_state:
        st.session_state.tx_type_radio = "Sale"
    if "expense_category" not in st.session_state:
        st.session_state.expense_category = EXPENSE_CATEGORIES[0]
    if "tx_desc" not in st.session_state:
        st.session_state.tx_desc = ""
    if "quick_expense_pending" not in st.session_state:
        st.session_state.quick_expense_pending = None


def open_gsheets_connection() -> tuple[GSheetsConnection | None, str | None]:
    try:
        return st.connection("gsheets", type=GSheetsConnection), None
    except Exception as e:
        return None, str(e)


def feedback_dual_save(res: dict, label: str) -> None:
    """Toast + banners after save (DB + Google Sheet)."""
    if res["postgres"] and res["sheets"]:
        st.toast(f"Tersimpan: {label}", icon="✅")
        st.success(f"{label} — tersimpan ke database dan Google Sheets.")
        return
    if res["postgres"]:
        st.toast(f"Tersimpan di database: {label}", icon="⚠️")
        st.warning("Tersimpan ke database. Google Sheets gagal — cek pesan di bawah.")
        for _, msg in res["errors"]:
            st.code(msg[:2000], language="text")
        return
    if res["sheets"]:
        st.toast(f"Tersimpan di Google Sheet: {label}", icon="⚠️")
        st.warning("Tersimpan ke Google Sheets. Database gagal — periksa koneksi Supabase.")
        for _, msg in res["errors"]:
            st.code(msg[:2000], language="text")
        return
    st.toast(f"Gagal simpan: {label}", icon="❌")
    st.error("Gagal menyimpan ke database dan ke Google Sheets.")
    for name, msg in res["errors"]:
        st.markdown(f"**{name}**")
        st.code(msg[:2000], language="text")


def page_entry():
    st.header("Catat kas harian")
    st.caption("Penjualan & pengeluaran tersimpan ke database dan Google Sheets.")

    ensure_session_defaults()

    today = date.today()
    engine_today, _ = get_db_engine()
    if engine_today:
        profit_today = load_today_profit(engine_today, today)
        st.markdown(
            "<p style='font-size:1.65rem;font-weight:800;margin:0 0 0.35rem 0;line-height:1.25;"
            "text-align:center;'>Total Profit Today: "
            f"{format_idr(profit_today)}</p>",
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            "<p style='font-size:1.65rem;font-weight:800;margin:0 0 0.35rem 0;"
            "text-align:center;color:#888;'>Total Profit Today: —</p>",
            unsafe_allow_html=True,
        )
        st.caption("Hubungkan database Supabase untuk melihat laba hari ini.")

    st.divider()

    tx_type = st.radio(
        "Jenis Transaksi",
        ["Sale", "Expense"],
        horizontal=True,
        key="tx_type_radio",
    )

    show_manual_form = False

    if tx_type == "Expense":
        expense_mode = st.radio(
            "Metode Pengeluaran",
            ["Quick Add", "Manual Entry"],
            horizontal=True,
            key="expense_mode_radio",
        )
        
        if expense_mode == "Quick Add":
            st.subheader("Quick Add Expenses")
            st.toggle(
                "Edit price before save",
                key="edit_quick_price",
                help="Nyalakan untuk mengubah harga preset sebelum disimpan (misal harga naik hari ini).",
            )

            cols = st.columns(3)
            for i, (item, data) in enumerate(QUICK_EXPENSES.items()):
                col = cols[i % 3]
                slug = "".join(ch if ch.isalnum() else "_" for ch in item)[:48]
                if col.button(f"➕ {item}", use_container_width=True, key=f"qe_{slug}"):
                    cat = normalize_quick_category(data["cat"])
                    if st.session_state.get("edit_quick_price"):
                        st.session_state.quick_expense_pending = {
                            "item": item,
                            "cat": cat,
                            "price": int(data["price"]),
                        }
                        st.rerun()
                    else:
                        amt = parse_amount(data["price"])
                        if amt is None:
                            st.error("Harga preset tidak valid.")
                        else:
                            gconn, gerr = open_gsheets_connection()
                            res = save_transaction_dual(
                                today,
                                "Expense",
                                cat,
                                amt,
                                item,
                                gsheets_conn=gconn,
                                gsheets_connect_error=gerr,
                            )
                            feedback_dual_save(res, item)
                            if res["postgres"] or res["sheets"]:
                                st.rerun()

            pending = st.session_state.quick_expense_pending
            if pending:
                pend_slug = "".join(ch if ch.isalnum() else "_" for ch in pending["item"])[:40]
                st.info(f"**Konfirmasi harga** — _{pending['item']}_")
                with st.form("confirm_quick_expense_price"):
                    new_amt = st.number_input(
                        "Jumlah (IDR)",
                        min_value=0.0,
                        value=float(pending["price"]),
                        step=1000.0,
                        format="%.0f",
                        key=f"qe_amt_{pend_slug}",
                    )
                    bc1, bc2 = st.columns(2)
                    with bc1:
                        submitted = st.form_submit_button("Simpan", type="primary", use_container_width=True)
                    with bc2:
                        cancelled = st.form_submit_button("Batal", use_container_width=True)

                    if cancelled:
                        st.session_state.quick_expense_pending = None
                        st.rerun()

                    if submitted:
                        amt = parse_amount(new_amt)
                        if amt is None or amt == 0:
                            st.error("Masukkan jumlah lebih dari 0.")
                        else:
                            gconn, gerr = open_gsheets_connection()
                            res = save_transaction_dual(
                                today,
                                "Expense",
                                pending["cat"],
                                amt,
                                pending["item"],
                                gsheets_conn=gconn,
                                gsheets_connect_error=gerr,
                            )
                            st.session_state.quick_expense_pending = None
                            feedback_dual_save(res, pending["item"])
                            if res["postgres"] or res["sheets"]:
                                st.rerun()
        else:
            show_manual_form = True
    else:
        show_manual_form = True

    if show_manual_form:
        st.subheader("Catat Manual" if tx_type == "Expense" else "Catat Penjualan")
        category = None
        if tx_type == "Expense":
            category = st.selectbox(
                "Kategori pengeluaran",
                EXPENSE_CATEGORIES,
                key="expense_category",
            )

        amt = st.number_input(
            "Jumlah (IDR)",
            min_value=0.0,
            value=0.0,
            step=1000.0,
            format="%.0f",
            help="Nominal dalam Rupiah.",
        )
        desc = st.text_input("Keterangan", key="tx_desc")

        tx_date = st.date_input("Tanggal", value=date.today(), key="tx_date")

        if st.button("Simpan transaksi", type="primary", use_container_width=True):
            amount = parse_amount(amt)
            if amount is None or amount == 0:
                st.error("Masukkan jumlah lebih dari 0.")
                return

            gsheets_conn, gsheets_err = open_gsheets_connection()

            res = save_transaction_dual(
                tx_date,
                tx_type,
                category if tx_type == "Expense" else None,
                amount,
                desc.strip() or None,
                gsheets_conn=gsheets_conn,
                gsheets_connect_error=gsheets_err,
            )

            feedback_dual_save(res, "Transaksi")
            if res["postgres"] or res["sheets"]:
                st.session_state.tx_desc = ""
                st.rerun()


def page_dashboard():
    st.header("Dashboard pemilik")
    pwd = get_dashboard_password()
    if not pwd:
        st.warning(
            "Atur password di `.streamlit/secrets.toml`: "
            "top-level `dashboard_password = \"...\"` **atau** di dalam `[dashboard]` sebagai `dashboard_password`."
        )

    if not st.session_state.owner_ok:
        entered = st.text_input("Password pemilik", type="password", key="owner_pwd")
        if st.button("Masuk", type="primary", use_container_width=True):
            if entered and str(entered).strip() == pwd:
                st.session_state.owner_ok = True
                st.rerun()
            else:
                st.error("Password salah.")
        return

    if st.button("Keluar", use_container_width=True):
        st.session_state.owner_ok = False
        st.rerun()

    today = date.today()
    col_y, col_m = st.columns(2)
    with col_y:
        year = st.number_input("Tahun", min_value=2020, max_value=2100, value=today.year, step=1)
    with col_m:
        month = st.number_input("Bulan", min_value=1, max_value=12, value=today.month, step=1)

    year_i, month_i = int(year), int(month)
    supabase, supa_err = get_supabase_client()
    totals = {"revenue": 0.0, "expense": 0.0, "net": 0.0}
    metrics_from_supabase = False

    if supabase:
        try:
            summary_resp = supabase.table("monthly_summary").select("*").execute()
            df_summary = pd.DataFrame(summary_resp.data or [])
            current = select_monthly_summary_row(df_summary, year_i, month_i)
            if current is not None:
                metrics_from_supabase = True
                totals["revenue"] = _df_series_float(
                    current,
                    "total_revenue",
                    "revenue",
                    "total_sales",
                    "sales",
                    "omzet",
                )
                totals["expense"] = _df_series_float(
                    current,
                    "total_expense",
                    "total_expenses",
                    "expense",
                    "total_cost",
                )
                totals["net"] = _df_series_float(
                    current,
                    "net_profit",
                    "net",
                    "profit",
                )
                if totals["net"] == 0.0 and (totals["revenue"] or totals["expense"]):
                    totals["net"] = totals["revenue"] - totals["expense"]
        except Exception as e:
            st.warning(f"Gagal memuat monthly_summary lewat Supabase: {e}")

    engine, eng_err = get_db_engine()
    if not metrics_from_supabase:
        if not engine:
            st.error(
                f"Tidak bisa memuat ringkasan. Supabase: {supa_err or '—'}. "
                f"PostgreSQL: {eng_err or '—'}"
            )
            return
        try:
            _, totals = load_monthly_aggregates(engine, year_i, month_i)
        except Exception as e:
            st.error(f"Gagal membaca data: {e}")
            st.code(traceback.format_exc(), language="text")
            return

    m1, m2, m3 = st.columns(3)
    with m1:
        st.metric("Total omzet", format_idr(totals["revenue"]))
    with m2:
        st.metric("Total biaya", format_idr(totals["expense"]))
    net = totals["net"]
    with m3:
        st.metric(
            "Net Profit This Month",
            format_idr(net),
            delta_color="inverse" if net < 0 else "normal",
        )

    st.subheader("Laba harian (garis)")
    if not engine:
        st.info("Sambungkan [postgres] di secrets untuk grafik laba harian dari tabel transaksi.")
        return

    try:
        df, _ = load_monthly_aggregates(engine, year_i, month_i)
    except Exception as e:
        st.warning(f"Grafik harian tidak bisa dimuat: {e}")
        return

    if df.empty or len(df) == 0:
        st.info("Belum ada data di bulan ini.")
        return

    fig = go.Figure()
    profit_labels = [format_idr(p) for p in df["profit"]]
    fig.add_trace(
        go.Scatter(
            x=df["date"],
            y=df["profit"],
            mode="lines+markers",
            name="Laba harian",
            line=dict(width=3),
            customdata=profit_labels,
            hovertemplate="%{x|%Y-%m-%d}<br>Laba: %{customdata}<extra></extra>",
        )
    )
    fig.update_layout(
        margin=dict(l=8, r=8, t=32, b=8),
        yaxis_title="IDR",
        xaxis_title="Tanggal",
        height=360,
        showlegend=False,
        hovermode="x unified",
    )
    fig.update_yaxes(tickprefix="Rp ")
    st.plotly_chart(fig, use_container_width=True)


def main():
    st.set_page_config(
        page_title="Warung Kas",
        page_icon="🏪",
        layout="centered",
        initial_sidebar_state="collapsed",
    )
    ensure_session_defaults()

    st.title("🏪 Warung — Kas & Laba")

    tab_log, tab_dash = st.tabs(["📝 Catat", "📊 Dashboard"])
    with tab_log:
        page_entry()
    with tab_dash:
        page_dashboard()


if __name__ == "__main__":
    main()
