"""
frontend.py - Smart Restaurant dashboard UI (Streamlit).

All screens, forms, tables, charts and layouts live here. This module never
talks to Supabase directly: it only calls the backend API object
(`backend.SmartRestaurantAPI`) and the helpers/constants listed below.

--------------------------------------------------------------------------
BACKEND CONTRACT (implemented in backend.py - Part 2)
--------------------------------------------------------------------------
Module level:
  is_configured() -> bool                  config_help() -> str
  format_pkr(amount) -> "Rs. 1,234"
  class AppError(Exception); class SessionExpired(AppError)
  ORDER_STATUSES  (list)   ORDER_TRANSITIONS  {status: [next statuses]}
  REQUEST_STATUSES (list)  REQUEST_TRANSITIONS {status: [next statuses]}
  REQUEST_TYPES   {code: label}

class SmartRestaurantAPI():
  # session
  is_authenticated (property) ; ctx (dict: user_id, email, full_name, role
      in {"super_admin","owner","waiter"} or None, restaurant_id,
      restaurant_name, employee_id)
  sign_in(email, password) ; sign_out() ; send_password_reset(email)
  verify_recovery(token_hash) ; update_password(new_password)
  get_public_settings() -> {login_help_text, forgot_password_text,
      global_announcement, subscription_reminder_text}
  get_labels() -> {label_key: text}
  get_access_gate() -> {allowed, status, plan_name, expiry_date, days_left,
      reason}
  list_messages() ; mark_message_read(message_id)
  # super admin
  admin_overview(date_from, date_to) ; admin_list_restaurants(search=None)
  admin_create_restaurant(name, phone, city, owner_name, owner_email,
      owner_password, plan_code) ; admin_update_restaurant(id, name, phone,
      city) ; admin_set_restaurant_status(id, status)
  admin_assign_owner(restaurant_id, email, full_name, password)
  admin_list_plans() ; admin_save_plan(code, name, price_pkr, duration_days,
      is_active, description)
  admin_get_subscription(restaurant_id) ; admin_set_subscription(
      restaurant_id, plan_code, status, start_date, expiry_date)
  admin_renew_subscription(restaurant_id, plan_code, days)
  admin_extend_subscription(restaurant_id, days)
  admin_send_expiry_reminders(within_days) -> int
  admin_list_payments(status=None) ; get_payment_proof_url(payment_id)
  admin_review_payment(payment_id, approve, note)
  admin_send_message(restaurant_id_or_None, title, body)
  admin_list_messages()
  admin_get_settings() -> dict ; admin_save_setting(key, value)
  ai_status() -> {configured, enabled, provider, providers, model,
      masked_key} ; admin_save_ai_settings(provider, model, enabled)
  ai_test_connection() -> (ok, message)
  admin_list_audit_logs(search, date_from, date_to, limit)
  # owner / waiter
  dashboard_summary() ; get_restaurant() ; update_restaurant(fields)
  upload_logo(data, filename, content_type) -> url
  list_categories() ; save_category(id_or_None, name, sort_order, is_active)
  list_menu_items() ; save_menu_item(payload) ; set_menu_item_active(id, bool)
  upload_menu_image(data, filename, content_type) -> url
  list_tables() ; create_table(label, seats) ; set_table_active(id, bool)
  regenerate_table_token(id) ; customer_base_url() ; build_table_url(token)
  make_qr_png(url) -> bytes
  list_orders(statuses, date_from, date_to, search, mine_only, limit)
  update_order_status(order_id, new_status) ; cancel_order(order_id, reason)
  refund_order(order_id, reason)
  list_requests(statuses, date_from, date_to)
  update_request_status(request_id, new_status)
  list_employees() ; create_employee(email, full_name, password, phone,
      commission_pct) ; update_employee(employee_id, full_name, phone,
      commission_pct, is_active)
  commission_report(date_from, date_to, waiter_id) ; commission_history(
      date_from, date_to, waiter_id)
  list_inventory() ; save_inventory_item(payload) ; adjust_stock(item_id,
      kind, quantity, reason) ; list_stock_movements(item_id, limit)
  list_expenses(date_from, date_to) ; add_expense(expense_date, category,
      description, amount) ; delete_expense(expense_id)
  analytics(date_from, date_to) ; waiter_stats(date_from, date_to)
  get_my_subscription() ; list_plans() ; list_my_payments()
  submit_payment_proof(plan_code, amount, reference, note, data, filename,
      content_type)
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

import backend

TZ = ZoneInfo("Asia/Karachi")
UTC = ZoneInfo("UTC")

# ---------------------------------------------------------------------------
# Labels (display names only - internal keys never change)
# ---------------------------------------------------------------------------
DEFAULT_LABELS: dict[str, str] = {
    "app_name": "Smart Restaurant",
    "app_tagline": "AI-Powered QR Ordering & Intelligent Restaurant Management",
    "sign_in": "Sign in",
    "sign_out": "Sign out",
    "nav_admin_overview": "Platform Overview",
    "nav_admin_restaurants": "Restaurants",
    "nav_admin_plans": "Plans & Pricing",
    "nav_admin_subscriptions": "Subscriptions",
    "nav_admin_payments": "Payment Review",
    "nav_admin_messages": "Messages",
    "nav_admin_settings": "System Settings",
    "nav_admin_ai": "AI Assistant",
    "nav_admin_audit": "Audit Logs",
    "nav_dashboard": "Dashboard",
    "nav_orders": "Orders",
    "nav_kitchen": "Kitchen",
    "nav_requests": "Customer Requests",
    "nav_menu": "Menu",
    "nav_tables": "Tables & QR",
    "nav_staff": "Waiters & Staff",
    "nav_commission": "Commission",
    "nav_inventory": "Inventory",
    "nav_expenses": "Expenses",
    "nav_analytics": "Sales Analytics",
    "nav_restaurant": "Restaurant Profile",
    "nav_subscription": "Subscription",
    "nav_messages": "Messages",
    "nav_my_stats": "My Statistics",
    "nav_account": "My Account",
}

NAV_ICONS = {
    "admin_overview": "📊", "admin_restaurants": "🏪", "admin_plans": "💳",
    "admin_subscriptions": "🗓️", "admin_payments": "🧾", "admin_messages": "📣",
    "admin_settings": "🛠️", "admin_ai": "🤖", "admin_audit": "📜",
    "dashboard": "📊", "orders": "🧾", "kitchen": "🍳", "requests": "🔔",
    "menu": "📖", "tables": "🪑", "staff": "🧑‍🍳", "commission": "💰",
    "inventory": "📦", "expenses": "💸", "analytics": "📈", "restaurant": "🏪",
    "subscription": "💳", "messages": "✉️", "my_stats": "📈", "account": "👤",
}

ROLE_PAGES = {
    "super_admin": [
        "admin_overview", "admin_restaurants", "admin_plans", "admin_subscriptions",
        "admin_payments", "admin_messages", "admin_settings", "admin_ai",
        "admin_audit", "account",
    ],
    "owner": [
        "dashboard", "orders", "kitchen", "requests", "menu", "tables", "staff",
        "commission", "inventory", "expenses", "analytics", "restaurant",
        "subscription", "messages", "account",
    ],
    "waiter": ["orders", "kitchen", "requests", "my_stats", "account"],
}
ROLE_TITLES = {"super_admin": "Super Admin", "owner": "Restaurant Owner", "waiter": "Waiter"}

ORDER_BADGE = {
    "received": "🆕 Received", "confirmed": "✅ Confirmed", "preparing": "👨‍🍳 Preparing",
    "ready": "🔔 Ready", "served": "🍽️ Served", "completed": "🏁 Completed",
    "cancelled": "❌ Cancelled",
}
REQUEST_BADGE = {
    "pending": "🟡 Pending", "accepted": "🔵 Accepted", "in_progress": "🟠 In progress",
    "completed": "🟢 Completed", "cancelled": "❌ Cancelled",
}
ACTIVE_ORDER_STATUSES = ["received", "confirmed", "preparing", "ready", "served"]
KITCHEN_STATUSES = ["received", "confirmed", "preparing", "ready"]
ACTIVE_REQUEST_STATUSES = ["pending", "accepted", "in_progress"]

PRESETS = ["Today", "Yesterday", "Last 7 days", "Last 30 days", "This month",
           "Previous month", "Custom range"]

THEMES = {
    "Emerald": dict(primary="#059669", bg="#f4f7f6", card="#ffffff", text="#0f172a",
                    sidebar="#064e3b", sidebar_text="#ecfdf5", border="#d1e7dd"),
    "Ocean": dict(primary="#2563eb", bg="#f3f6fb", card="#ffffff", text="#0f172a",
                  sidebar="#1e3a8a", sidebar_text="#eff6ff", border="#d6e0f5"),
    "Sunset": dict(primary="#ea580c", bg="#fbf6f2", card="#ffffff", text="#1c1917",
                   sidebar="#7c2d12", sidebar_text="#fff7ed", border="#f0ddd0"),
    "Midnight": dict(primary="#8b5cf6", bg="#0f172a", card="#1e293b", text="#e2e8f0",
                     sidebar="#020617", sidebar_text="#e2e8f0", border="#334155"),
}


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def t(key: str) -> str:
    """Look up a (possibly customised) display label."""
    return st.session_state.get("labels", {}).get(key) or DEFAULT_LABELS.get(key, key)


def load_labels(api) -> None:
    if "labels" not in st.session_state:
        try:
            st.session_state["labels"] = api.get_labels() or {}
        except backend.AppError:
            st.session_state["labels"] = {}


def today() -> date:
    return datetime.now(TZ).date()


def _parse_dt(value) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def fmt_dt(value) -> str:
    dt = _parse_dt(value)
    return dt.astimezone(TZ).strftime("%d %b %Y, %I:%M %p") if dt else "—"


def fmt_duration(start, end) -> str:
    a, b = _parse_dt(start), _parse_dt(end)
    if not a or not b:
        return "—"
    secs = max(int((b - a).total_seconds()), 0)
    if secs < 60:
        return f"{secs}s"
    return f"{secs // 60}m {secs % 60}s"


def _money(value) -> str:
    if value is None or pd.isna(value):
        return "—"
    return backend.format_pkr(value)


def flash(message: str, kind: str = "success") -> None:
    st.session_state["_flash"] = (kind, message)


def show_flash() -> None:
    item = st.session_state.pop("_flash", None)
    if item:
        kind, message = item
        {"success": st.success, "warning": st.warning, "error": st.error}.get(kind, st.info)(message)


def attempt(fn, *args, **kwargs):
    """Run a backend call. Returns (ok, value); shows a friendly error on failure."""
    try:
        return True, fn(*args, **kwargs)
    except backend.SessionExpired:
        raise
    except backend.AppError as exc:
        st.error(str(exc))
        return False, None


def _rerun() -> None:
    try:
        st.rerun(scope="fragment")
    except Exception:  # not inside a fragment
        st.rerun()


def show_table(rows, columns: dict, money=(), dts=(), empty="No records found.") -> None:
    """Render a list of dicts as a table. `columns` maps key -> header."""
    if not rows:
        st.info(empty)
        return
    df = pd.DataFrame(rows)
    for key in columns:
        if key not in df.columns:
            df[key] = None
    df = df[list(columns)].copy()
    for key in money:
        df[key] = df[key].map(_money)
    for key in dts:
        df[key] = df[key].map(fmt_dt)
    st.dataframe(df.rename(columns=columns), hide_index=True)


def kpi_row(items: list[tuple[str, str]]) -> None:
    for start in range(0, len(items), 4):
        chunk = items[start:start + 4]
        cols = st.columns(len(chunk))
        for col, (label, value) in zip(cols, chunk):
            col.metric(label, value)


def date_filter(key: str, default: str = "Today") -> tuple[date, date]:
    c1, c2 = st.columns([2, 3])
    preset = c1.selectbox("Period", PRESETS, index=PRESETS.index(default), key=f"{key}_preset")
    now = today()
    if preset == "Today":
        start = end = now
    elif preset == "Yesterday":
        start = end = now - timedelta(days=1)
    elif preset == "Last 7 days":
        start, end = now - timedelta(days=6), now
    elif preset == "Last 30 days":
        start, end = now - timedelta(days=29), now
    elif preset == "This month":
        start, end = now.replace(day=1), now
    elif preset == "Previous month":
        end = now.replace(day=1) - timedelta(days=1)
        start = end.replace(day=1)
    else:
        picked = c2.date_input("Dates", value=(now - timedelta(days=6), now), key=f"{key}_custom")
        if isinstance(picked, (list, tuple)) and len(picked) == 2:
            start, end = picked
        else:
            c2.info("Pick both a start and an end date.")
            start, end = now - timedelta(days=6), now
    if preset != "Custom range":
        c2.caption(f"{start:%d %b %Y} → {end:%d %b %Y}")
    return start, end


def read_upload(file, max_mb: int):
    """Return (bytes, filename, content_type) for a Streamlit upload, or None."""
    if file is None:
        return None
    data = file.getvalue()
    if len(data) > max_mb * 1024 * 1024:
        raise backend.AppError(f"The file is too large (maximum {max_mb} MB).")
    return data, file.name, file.type or "application/octet-stream"


def valid_password(password: str) -> str | None:
    if len(password) < 8:
        return "Password must be at least 8 characters."
    if not any(c.isalpha() for c in password) or not any(c.isdigit() for c in password):
        return "Password must contain at least one letter and one number."
    return None


BEEP_JS = """<script>
try{const C=window.AudioContext||window.webkitAudioContext;const c=new C();
const o=c.createOscillator();const g=c.createGain();o.connect(g);g.connect(c.destination);
o.frequency.value=880;g.gain.value=0.15;o.start();setTimeout(function(){o.stop();c.close();},350);}catch(e){}
</script>"""


def play_alert() -> None:
    """Short beep. Browsers may block sound until the user has tapped the page once."""
    components.html(BEEP_JS, height=0)


def _alert_if_increased(key: str, count: int, message: str, sound: bool) -> None:
    previous = st.session_state.get(key)
    st.session_state[key] = count
    if previous is not None and count > previous:
        st.toast(message, icon="🔔")
        if sound:
            play_alert()


# ---------------------------------------------------------------------------
# Theme
# ---------------------------------------------------------------------------
def inject_theme() -> None:
    th = THEMES.get(st.session_state.get("theme", "Emerald"), THEMES["Emerald"])
    dark = st.session_state.get("theme") == "Midnight"
    extra = (
        f".stApp h1,.stApp h2,.stApp h3,.stApp h4,.stApp p,.stApp label,.stApp li,"
        f".stApp span{{color:{th['text']};}}" if dark else ""
    )
    st.markdown(
        f"""<style>
        .stApp{{background:{th['bg']};color:{th['text']};}}
        .block-container{{padding-top:1.5rem;max-width:1200px;}}
        [data-testid="stSidebar"]{{background:{th['sidebar']};}}
        [data-testid="stSidebar"] label,[data-testid="stSidebar"] p,
        [data-testid="stSidebar"] h1,[data-testid="stSidebar"] h2,
        [data-testid="stSidebar"] h3,[data-testid="stSidebar"] .stCaption{{color:{th['sidebar_text']} !important;}}
        div.stButton>button[kind="primary"],.stFormSubmitButton>button[kind="primary"]{{
            background:{th['primary']};border:none;color:#fff;border-radius:10px;}}
        div.stButton>button,.stDownloadButton>button{{border-radius:10px;}}
        [data-testid="stMetric"]{{background:{th['card']};border:1px solid {th['border']};
            border-radius:12px;padding:12px 16px;}}
        [data-testid="stMetricValue"],[data-testid="stMetricLabel"]{{color:{th['text']};}}
        {extra}
        </style>""",
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Setup / login / recovery
# ---------------------------------------------------------------------------
def render_setup_help(message: str) -> None:
    st.title("🍽️ Smart Restaurant")
    st.error("The application is not configured yet.")
    st.markdown(message)


def render_no_access(api) -> None:
    st.title("🍽️ Smart Restaurant")
    st.warning(
        "Your account is signed in but has no active role. Please contact your "
        "restaurant owner or the platform administrator."
    )
    if st.button("Sign out"):
        sign_out(api)


def sign_out(api, message: str | None = None) -> None:
    try:
        api.sign_out()
    except backend.AppError:
        pass
    for key in list(st.session_state.keys()):
        del st.session_state[key]
    if message:
        flash(message)
    st.rerun()


def render_login(api) -> None:
    _, mid, _ = st.columns([1, 2, 1])
    with mid:
        st.markdown(f"# 🍽️ {t('app_name')}")
        st.caption(t("app_tagline"))
        show_flash()
        settings = {}
        try:
            settings = api.get_public_settings() or {}
        except backend.AppError:
            pass
        if settings.get("global_announcement"):
            st.info(settings["global_announcement"])

        tab_in, tab_forgot = st.tabs([t("sign_in"), "Forgot password"])
        with tab_in:
            if settings.get("login_help_text"):
                st.caption(settings["login_help_text"])
            with st.form("login_form"):
                email = st.text_input("Email", autocomplete="username")
                password = st.text_input("Password", type="password", autocomplete="current-password")
                submitted = st.form_submit_button(t("sign_in"), type="primary")
            if submitted:
                if not email.strip() or not password:
                    st.error("Please enter your email and password.")
                else:
                    with st.spinner("Signing in..."):
                        ok, _ = attempt(api.sign_in, email.strip(), password)
                    if ok:
                        st.session_state.pop("labels", None)
                        st.rerun()
        with tab_forgot:
            st.caption(settings.get("forgot_password_text")
                       or "Enter your account email and we will send you a reset link.")
            with st.form("forgot_form"):
                email = st.text_input("Account email")
                submitted = st.form_submit_button("Send reset link", type="primary")
            if submitted:
                if "@" not in email:
                    st.error("Please enter a valid email address.")
                else:
                    attempt(api.send_password_reset, email.strip())
                    # Same message whether or not the account exists (no user enumeration).
                    st.success("If that email belongs to an account, a reset link is on its way.")


def render_recovery(api, token_hash: str) -> None:
    _, mid, _ = st.columns([1, 2, 1])
    with mid:
        st.markdown(f"# 🍽️ {t('app_name')}")
        st.subheader("Set a new password")
        if not st.session_state.get("_recovery_ok"):
            with st.spinner("Verifying your reset link..."):
                ok, _ = attempt(api.verify_recovery, token_hash)
            if not ok:
                st.warning("This reset link is invalid or has expired. Please request a new one.")
                if st.button("Back to sign in"):
                    st.query_params.clear()
                    st.rerun()
                return
            st.session_state["_recovery_ok"] = True
        with st.form("recovery_form"):
            new = st.text_input("New password", type="password")
            confirm = st.text_input("Confirm new password", type="password")
            submitted = st.form_submit_button("Update password", type="primary")
        if submitted:
            problem = valid_password(new) or (None if new == confirm else "Passwords do not match.")
            if problem:
                st.error(problem)
            else:
                ok, _ = attempt(api.update_password, new)
                if ok:
                    st.query_params.clear()
                    sign_out(api, "Password updated. Please sign in with your new password.")


# ---------------------------------------------------------------------------
# Shell: navigation, banners, routing
# ---------------------------------------------------------------------------
def allowed_pages(role: str, gate: dict) -> list[str]:
    pages = ROLE_PAGES.get(role, [])
    if role != "super_admin" and not gate.get("allowed", True):
        return ["subscription", "messages", "account"] if role == "owner" else ["account"]
    return pages


def render_sidebar(api, pages: list[str], gate: dict) -> str:
    ctx = api.ctx
    with st.sidebar:
        st.markdown(f"## 🍽️ {t('app_name')}")
        st.caption(f"{ctx.get('restaurant_name') or 'Platform'}  \n"
                   f"{ctx.get('full_name') or ctx.get('email')} · {ROLE_TITLES.get(ctx['role'], '')}")
        if st.session_state.get("nav_page") not in pages:
            st.session_state["nav_page"] = pages[0]
        page = st.radio(
            "Navigation", pages, key="nav_page", label_visibility="collapsed",
            format_func=lambda k: f"{NAV_ICONS.get(k, '•')}  {t('nav_' + k)}",
        )
        st.divider()
        st.selectbox("Theme", list(THEMES), key="theme")
        if st.button(t("sign_out")):
            sign_out(api)
    return page


def render_announcement(api) -> None:
    show_flash()
    try:
        text = (api.get_public_settings() or {}).get("global_announcement")
    except backend.AppError:
        text = None
    if text:
        st.info(f"📣 {text}")


def render_subscription_banner(gate: dict) -> None:
    if gate.get("reason") is None and gate.get("allowed", True):
        days = gate.get("days_left")
        if days is not None and days <= 7:
            st.warning(f"⏳ Your {gate.get('plan_name', 'subscription')} ends in {days} day(s) "
                       f"({gate.get('expiry_date')}). Renew from the Subscription page.")
    elif not gate.get("allowed", True):
        st.error(f"🔒 {gate.get('reason') or 'Access is currently restricted.'}")


def render_page(api, page: str) -> None:
    func = PAGES.get(page)
    if func is None:
        st.error("Page not found.")
        return
    func(api)


def _title(key: str, subtitle: str | None = None) -> None:
    st.title(f"{NAV_ICONS.get(key, '')} {t('nav_' + key)}")
    if subtitle:
        st.caption(subtitle)


# ---------------------------------------------------------------------------
# Account (all roles)
# ---------------------------------------------------------------------------
def page_account(api) -> None:
    _title("account")
    ctx = api.ctx
    st.markdown(f"**Name:** {ctx.get('full_name') or '—'}  \n**Email:** {ctx.get('email')}  \n"
                f"**Role:** {ROLE_TITLES.get(ctx['role'], ctx['role'])}  \n"
                f"**Restaurant:** {ctx.get('restaurant_name') or 'Platform'}")
    st.subheader("Change password")
    with st.form("pw_form"):
        new = st.text_input("New password", type="password")
        confirm = st.text_input("Confirm new password", type="password")
        submitted = st.form_submit_button("Update password", type="primary")
    if submitted:
        problem = valid_password(new) or (None if new == confirm else "Passwords do not match.")
        if problem:
            st.error(problem)
        else:
            ok, _ = attempt(api.update_password, new)
            if ok:
                st.success("Password updated.")
    st.caption("Forgot your password? Sign out and use “Forgot password” on the sign-in page.")


# ---------------------------------------------------------------------------
# SUPER ADMIN pages
# ---------------------------------------------------------------------------
def page_admin_overview(api) -> None:
    _title("admin_overview")
    start, end = date_filter("adm_ov", "Last 30 days")
    ok, ov = attempt(api.admin_overview, start, end)
    if not ok:
        return
    kpi_row([
        ("Total restaurants", str(ov["total_restaurants"])),
        ("Active restaurants", str(ov["active_restaurants"])),
        ("Trial accounts", str(ov["trial_accounts"])),
        ("Expired subscriptions", str(ov["expired_subscriptions"])),
        ("Suspended", str(ov["suspended_restaurants"])),
        ("Pending payments", str(ov["pending_payments"])),
        ("Orders (period)", str(ov["total_orders"])),
        ("Sales (period)", _money(ov["total_sales"])),
    ])
    st.subheader("Daily platform sales")
    daily = ov.get("daily_sales") or []
    if daily:
        df = pd.DataFrame(daily)
        st.bar_chart(df.set_index("date")["sales"])
    else:
        st.info("No sales in this period.")
    st.subheader("Top restaurants")
    show_table(ov.get("top_restaurants"), {"restaurant": "Restaurant", "orders": "Orders",
                                           "sales": "Sales"}, money=("sales",))


def page_admin_restaurants(api) -> None:
    _title("admin_restaurants")
    tab_list, tab_new = st.tabs(["All restaurants", "Create restaurant"])

    with tab_list:
        _admin_restaurant_list(api)

    with tab_new:
        _admin_restaurant_create(api)


def _admin_restaurant_list(api) -> None:
    # Own function so an early "return" (no restaurants yet) cannot hide the other tab.
    if True:
        search = st.text_input("Search by name, city or owner email", key="adm_r_search")
        ok, rows = attempt(api.admin_list_restaurants, search or None)
        if not ok:
            return
        show_table(rows, {"name": "Restaurant", "city": "City", "owner_email": "Owner",
                          "status": "Status", "plan_name": "Plan", "sub_status": "Subscription",
                          "expiry_date": "Expires"})
        if not rows:
            return
        by_id = {r["id"]: r for r in rows}
        rid = st.selectbox("Manage restaurant", list(by_id), format_func=lambda i: by_id[i]["name"],
                           key="adm_r_pick")
        r = by_id[rid]
        st.markdown(f"**Status:** {r['status']} · **Owner:** {r.get('owner_email') or 'not assigned'}")
        with st.form(f"adm_r_edit_{rid}"):
            name = st.text_input("Name", value=r["name"])
            phone = st.text_input("Phone", value=r.get("phone") or "")
            city = st.text_input("City", value=r.get("city") or "")
            if st.form_submit_button("Save details", type="primary"):
                if not name.strip():
                    st.error("Restaurant name is required.")
                else:
                    ok, _ = attempt(api.admin_update_restaurant, rid, name.strip(), phone.strip(), city.strip())
                    if ok:
                        flash("Restaurant updated.")
                        st.rerun()

        c1, c2 = st.columns(2)
        with c1:
            if r["status"] == "suspended":
                if st.button("▶️ Activate restaurant", key=f"act_{rid}", type="primary"):
                    ok, _ = attempt(api.admin_set_restaurant_status, rid, "active")
                    if ok:
                        flash("Restaurant activated.")
                        st.rerun()
            else:
                with st.expander("⏸️ Suspend restaurant"):
                    st.warning("Owner and waiters will lose access until you activate it again.")
                    sure = st.checkbox("I understand", key=f"susp_c_{rid}")
                    if st.button("Suspend", key=f"susp_{rid}", disabled=not sure):
                        ok, _ = attempt(api.admin_set_restaurant_status, rid, "suspended")
                        if ok:
                            flash("Restaurant suspended.", "warning")
                            st.rerun()
        with c2:
            with st.expander("👤 Assign or replace owner"):
                st.caption("Use an existing owner email, or enter a name and temporary password "
                           "to create a new owner account.")
                with st.form(f"owner_{rid}"):
                    o_email = st.text_input("Owner email")
                    o_name = st.text_input("Owner full name")
                    o_pw = st.text_input("Temporary password (new accounts only)", type="password")
                    if st.form_submit_button("Assign owner"):
                        problem = None if not o_pw else valid_password(o_pw)
                        if "@" not in o_email:
                            st.error("Enter a valid email.")
                        elif problem:
                            st.error(problem)
                        else:
                            ok, _ = attempt(api.admin_assign_owner, rid, o_email.strip(),
                                            o_name.strip(), o_pw or None)
                            if ok:
                                flash("Owner assigned.")
                                st.rerun()

def _admin_restaurant_create(api) -> None:
    if True:
        ok, plans = attempt(api.admin_list_plans)
        plan_codes = [p["code"] for p in plans] if ok and plans else ["free_trial"]
        plan_names = {p["code"]: p["name"] for p in (plans or [])}
        with st.form("adm_new_restaurant"):
            st.markdown("**Restaurant**")
            name = st.text_input("Restaurant name *")
            c1, c2 = st.columns(2)
            phone = c1.text_input("Phone")
            city = c2.text_input("City")
            plan = st.selectbox("Starting plan", plan_codes, format_func=lambda c: plan_names.get(c, c))
            st.markdown("**Owner account**")
            o_name = st.text_input("Owner full name *")
            o_email = st.text_input("Owner email *")
            o_pw = st.text_input("Temporary password *", type="password",
                                 help="Share it with the owner privately. They can change it after signing in.")
            submitted = st.form_submit_button("Create restaurant and owner", type="primary")
        if submitted:
            problem = (
                "Restaurant name is required." if not name.strip()
                else "Owner name is required." if not o_name.strip()
                else "Enter a valid owner email." if "@" not in o_email
                else valid_password(o_pw)
            )
            if problem:
                st.error(problem)
            else:
                with st.spinner("Creating..."):
                    ok, _ = attempt(api.admin_create_restaurant, name.strip(), phone.strip(), city.strip(),
                                    o_name.strip(), o_email.strip(), o_pw, plan)
                if ok:
                    flash(f"Restaurant “{name.strip()}” created.")
                    st.rerun()


def page_admin_plans(api) -> None:
    _title("admin_plans", "Prices are set by you. Nothing is pre-filled. The Free Trial duration is the trial length for new restaurants.")
    ok, plans = attempt(api.admin_list_plans)
    if not ok:
        return
    for p in plans:
        with st.expander(f"{p['name']}  ·  {_money(p['price_pkr'])}  ·  {p['duration_days']} days",
                         expanded=False):
            with st.form(f"plan_{p['code']}"):
                name = st.text_input("Display name", value=p["name"])
                c1, c2 = st.columns(2)
                price = c1.number_input("Price (Rs.)", min_value=0.0, step=100.0,
                                        value=float(p["price_pkr"] or 0), format="%.2f")
                days = c2.number_input("Duration (days)", min_value=1, max_value=3660,
                                       value=int(p["duration_days"] or 30), step=1)
                desc = st.text_area("Description", value=p.get("description") or "")
                active = st.checkbox("Plan is active", value=bool(p.get("is_active", True)))
                if st.form_submit_button("Save plan", type="primary"):
                    if not name.strip():
                        st.error("Name is required.")
                    else:
                        ok, _ = attempt(api.admin_save_plan, p["code"], name.strip(), price,
                                        int(days), active, desc.strip())
                        if ok:
                            flash("Plan saved.")
                            st.rerun()


def page_admin_subscriptions(api) -> None:
    _title("admin_subscriptions")
    ok, rows = attempt(api.admin_list_restaurants, None)
    if not ok:
        return
    show_table(rows, {"name": "Restaurant", "plan_name": "Plan", "sub_status": "Status",
                      "expiry_date": "Expires", "days_left": "Days left"})
    if not rows:
        return
    ok, plans = attempt(api.admin_list_plans)
    plan_codes = [p["code"] for p in plans] if ok and plans else []
    plan_names = {p["code"]: p["name"] for p in (plans or [])}
    by_id = {r["id"]: r for r in rows}
    rid = st.selectbox("Restaurant", list(by_id), format_func=lambda i: by_id[i]["name"], key="adm_s_pick")
    ok, sub = attempt(api.admin_get_subscription, rid)
    if ok and sub:
        st.info(f"Current: **{plan_names.get(sub['plan_code'], sub['plan_code'])}** · {sub['status']} · "
                f"{sub['start_date']} → {sub['expiry_date']}")

    tab_renew, tab_ext, tab_manual, tab_rem = st.tabs(["Renew", "Extend", "Set manually", "Reminders"])
    with tab_renew:
        st.caption("Renewing extends from the later of today and the current expiry date.")
        with st.form("renew_form"):
            plan = st.selectbox("Plan", plan_codes, format_func=lambda c: plan_names.get(c, c))
            days = st.number_input("Days to add", min_value=1, max_value=3660, value=30, step=1)
            if st.form_submit_button("Renew subscription", type="primary"):
                ok, _ = attempt(api.admin_renew_subscription, rid, plan, int(days))
                if ok:
                    flash("Subscription renewed.")
                    st.rerun()
    with tab_ext:
        with st.form("extend_form"):
            days = st.number_input("Extra days (keeps the current plan)", min_value=1, max_value=3660,
                                   value=7, step=1)
            if st.form_submit_button("Extend", type="primary"):
                ok, _ = attempt(api.admin_extend_subscription, rid, int(days))
                if ok:
                    flash("Subscription extended.")
                    st.rerun()
    with tab_manual:
        statuses = ["trial", "active", "expired", "suspended"]
        with st.form("manual_sub_form"):
            plan = st.selectbox("Plan", plan_codes, format_func=lambda c: plan_names.get(c, c), key="ms_plan")
            status = st.selectbox("Status", statuses)
            c1, c2 = st.columns(2)
            s_date = c1.date_input("Start date", value=today())
            e_date = c2.date_input("Expiry date", value=today() + timedelta(days=30))
            if st.form_submit_button("Save subscription", type="primary"):
                if e_date < s_date:
                    st.error("Expiry date must be on or after the start date.")
                else:
                    ok, _ = attempt(api.admin_set_subscription, rid, plan, status, s_date, e_date)
                    if ok:
                        flash("Subscription saved.")
                        st.rerun()
    with tab_rem:
        st.caption("Sends an in-app message to every restaurant whose subscription ends soon.")
        within = st.number_input("Remind restaurants expiring within (days)", min_value=1, max_value=60,
                                 value=7, step=1)
        if st.button("Send expiry reminders"):
            ok, count = attempt(api.admin_send_expiry_reminders, int(within))
            if ok:
                st.success(f"Reminders sent to {count} restaurant(s).")


def page_admin_payments(api) -> None:
    _title("admin_payments", "Uploading a receipt never approves a payment. Only you can approve it.")
    status = st.radio("Show", ["pending", "approved", "rejected", "all"], horizontal=True, key="pay_status")
    ok, rows = attempt(api.admin_list_payments, None if status == "all" else status)
    if not ok:
        return
    if not rows:
        st.info("No payments in this view.")
        return
    for p in rows:
        with st.container(border=True):
            st.markdown(f"**{p['restaurant_name']}** · {p.get('plan_name') or p['plan_code']} · "
                        f"**{_money(p['amount'])}**")
            st.caption(f"Submitted {fmt_dt(p['created_at'])} · Reference: {p.get('reference') or '—'} · "
                       f"Status: {p['status']}")
            if p.get("note"):
                st.write(f"📝 {p['note']}")
            if p.get("rejection_reason"):
                st.write(f"❌ Rejection reason: {p['rejection_reason']}")
            url_key = f"proof_{p['id']}"
            if p.get("proof_path"):
                if st.button("🔎 Load receipt", key=f"load_{p['id']}"):
                    ok, url = attempt(api.get_payment_proof_url, p["id"])
                    if ok:
                        st.session_state[url_key] = url
                if st.session_state.get(url_key):
                    st.link_button("Open receipt (link expires soon)", st.session_state[url_key])
            if p["status"] == "pending":
                note = st.text_input("Note / rejection reason", key=f"note_{p['id']}")
                c1, c2 = st.columns(2)
                if c1.button("✅ Approve and renew", key=f"ok_{p['id']}", type="primary"):
                    ok, _ = attempt(api.admin_review_payment, p["id"], True, note.strip())
                    if ok:
                        flash("Payment approved and subscription renewed.")
                        st.rerun()
                if c2.button("❌ Reject", key=f"no_{p['id']}"):
                    if not note.strip():
                        st.error("Please enter a rejection reason.")
                    else:
                        ok, _ = attempt(api.admin_review_payment, p["id"], False, note.strip())
                        if ok:
                            flash("Payment rejected.", "warning")
                            st.rerun()


def page_admin_messages(api) -> None:
    _title("admin_messages")
    ok, rows = attempt(api.admin_list_restaurants, None)
    if not ok:
        return
    by_id = {r["id"]: r["name"] for r in rows}
    with st.form("send_msg"):
        target = st.selectbox("Send to", ["ALL"] + list(by_id),
                              format_func=lambda i: "All restaurants" if i == "ALL" else by_id[i])
        title = st.text_input("Title")
        body = st.text_area("Message")
        if st.form_submit_button("Send message", type="primary"):
            if not title.strip() or not body.strip():
                st.error("Title and message are required.")
            else:
                ok, _ = attempt(api.admin_send_message, None if target == "ALL" else target,
                                title.strip(), body.strip())
                if ok:
                    flash("Message sent.")
                    st.rerun()
    st.subheader("Sent messages")
    ok, msgs = attempt(api.admin_list_messages)
    if ok:
        show_table(msgs, {"created_at": "Sent", "restaurant_name": "To", "title": "Title", "body": "Message"},
                   dts=("created_at",))


def page_admin_settings(api) -> None:
    _title("admin_settings", "Labels are display names only. Changing them never affects permissions or data.")
    ok, settings = attempt(api.admin_get_settings)
    if not ok:
        return
    tab_text, tab_labels = st.tabs(["System messages", "Display labels"])
    with tab_text:
        with st.form("sys_text"):
            login_help = st.text_area("Login help text", value=settings.get("login_help_text") or "")
            forgot = st.text_area("Forgot-password explanation", value=settings.get("forgot_password_text") or "")
            announce = st.text_area("Global announcement (shown to everyone; leave empty to hide)",
                                    value=settings.get("global_announcement") or "")
            reminder = st.text_area("Subscription reminder text",
                                    value=settings.get("subscription_reminder_text") or "")
            rem_days = st.number_input("Show expiry warnings when days left ≤", min_value=1, max_value=60,
                                       value=int(settings.get("expiry_reminder_days") or 7))
            if st.form_submit_button("Save", type="primary"):
                for key, val in [("login_help_text", login_help), ("forgot_password_text", forgot),
                                 ("global_announcement", announce), ("subscription_reminder_text", reminder),
                                 ("expiry_reminder_days", int(rem_days))]:
                    ok, _ = attempt(api.admin_save_setting, key, val if isinstance(val, int) else val.strip())
                    if not ok:
                        break
                else:
                    flash("Settings saved.")
                    st.rerun()
    with tab_labels:
        current = settings.get("ui_labels") or {}
        with st.form("labels_form"):
            new_vals = {}
            cols = st.columns(2)
            for i, (key, default) in enumerate(DEFAULT_LABELS.items()):
                new_vals[key] = cols[i % 2].text_input(default, value=current.get(key, ""),
                                                       placeholder=default, max_chars=60, key=f"lbl_{key}")
            c1, c2 = st.columns(2)
            save = c1.form_submit_button("Save labels", type="primary")
            reset = c2.form_submit_button("Reset all to defaults")
        if save or reset:
            payload = {} if reset else {k: v.strip() for k, v in new_vals.items() if v.strip() and v.strip() != DEFAULT_LABELS[k]}
            ok, _ = attempt(api.admin_save_setting, "ui_labels", payload)
            if ok:
                st.session_state.pop("labels", None)
                flash("Labels saved." if save else "Labels reset.")
                st.rerun()


def page_admin_ai(api) -> None:
    _title("admin_ai", "Optional. The app works fully without AI; basic recommendations always work.")
    ok, status = attempt(api.ai_status)
    if not ok:
        return
    if status["configured"]:
        st.success(f"AI API key detected (stored only in Supabase): `{status['masked_key']}`")
    else:
        st.info("No AI API key found. Add `GEMINI_API_KEY` as a Supabase Edge Function secret to enable AI "
                "(see the README). Basic recommendations keep working.")
    with st.form("ai_form"):
        providers = status["providers"]
        provider = st.selectbox("Provider", providers,
                                index=providers.index(status["provider"]) if status["provider"] in providers else 0)
        model = st.text_input("Model name", value=status.get("model") or "",
                              help="Use a current model name from Google AI Studio.")
        enabled = st.checkbox("Enable AI assistant for customers", value=bool(status["enabled"]))
        if st.form_submit_button("Save AI settings", type="primary"):
            ok, _ = attempt(api.admin_save_ai_settings, provider, model.strip(), enabled)
            if ok:
                flash("AI settings saved.")
                st.rerun()
    if st.button("Test connection"):
        with st.spinner("Contacting the AI provider..."):
            ok, result = attempt(api.ai_test_connection)
        if ok:
            good, msg = result
            (st.success if good else st.error)(msg)
    st.caption("🔐 The API key is never stored in the database or in this dashboard. It lives only in Supabase "
               "Edge Function secrets. To remove or change it, open Supabase → Edge Functions → Secrets and "
               "delete or replace `GEMINI_API_KEY`.")


def page_admin_audit(api) -> None:
    _title("admin_audit")
    start, end = date_filter("audit", "Last 7 days")
    search = st.text_input("Search action, user or entity")
    ok, rows = attempt(api.admin_list_audit_logs, search or None, start, end, 300)
    if ok:
        for r in rows or []:
            r["details"] = str(r.get("details") or "")
        show_table(rows, {"created_at": "When", "actor_email": "User", "action": "Action",
                          "entity": "Entity", "details": "Details"}, dts=("created_at",))


# ---------------------------------------------------------------------------
# ORDERS / KITCHEN
# ---------------------------------------------------------------------------
def _order_card(api, o: dict, prefix: str, allow_cancel: bool) -> None:
    status = o["status"]
    with st.container(border=True):
        h1, h2 = st.columns([3, 2])
        h1.markdown(f"**#{o['order_number']}** · Table **{o.get('table_label') or '?'}**")
        h2.markdown(f"{ORDER_BADGE.get(status, status)} · **{_money(o['total'])}**")
        st.caption(f"Placed {fmt_dt(o['created_at'])}"
                   + (f" · Waiter: {o['waiter_name']}" if o.get("waiter_name") else ""))
        for it in o.get("items", []):
            line = f"{it['quantity']} × {it['name']}"
            if it.get("note"):
                line += f" — _{it['note']}_"
            st.markdown(f"- {line}")
        if o.get("notes"):
            st.info(f"📝 {o['notes']}")
        nexts = backend.ORDER_TRANSITIONS.get(status, [])
        forward = [s for s in nexts if s != "cancelled"]
        can_cancel = allow_cancel and "cancelled" in nexts
        n = len(forward) + (1 if can_cancel else 0)
        if n == 0:
            return
        cols = st.columns(n)
        for col, nxt in zip(cols, forward):
            if col.button(f"Mark {nxt.replace('_', ' ')}", key=f"{prefix}_{o['id']}_{nxt}",
                          type="primary" if nxt == forward[0] else "secondary"):
                ok, _ = attempt(api.update_order_status, o["id"], nxt)
                if ok:
                    _rerun()
        if can_cancel:
            with cols[-1].popover("Cancel order"):
                reason = st.text_input("Reason", key=f"{prefix}_{o['id']}_reason")
                if st.button("Confirm cancellation", key=f"{prefix}_{o['id']}_cancel"):
                    ok, _ = attempt(api.cancel_order, o["id"], reason.strip())
                    if ok:
                        _rerun()


def _live_orders(api, key: str, statuses: list[str], group: bool, allow_cancel: bool) -> None:
    c1, c2, c3 = st.columns([2, 2, 2])
    auto = c1.toggle("Auto-refresh (10s)", value=True, key=f"{key}_auto")
    sound = c2.toggle("🔔 Sound alert", value=False, key=f"{key}_sound",
                      help="Your browser may need one tap on the page before it allows sound.")
    mine = c3.toggle("Only my orders", value=False, key=f"{key}_mine") if api.ctx["role"] == "waiter" else False
    if sound and st.button("Test sound", key=f"{key}_testsound"):
        play_alert()

    @st.fragment(run_every=10 if auto else None)
    def board() -> None:
        ok, orders = attempt(api.list_orders, statuses, None, None, None, mine, 100)
        if not ok:
            return
        new_count = sum(1 for o in orders if o["status"] == "received")
        _alert_if_increased(f"{key}_seen", new_count, "New order received!", sound)
        st.caption(f"{len(orders)} open order(s) · updated {datetime.now(TZ):%I:%M:%S %p}")
        if not orders:
            st.info("No open orders right now.")
            return
        if group:
            for status in statuses:
                subset = [o for o in orders if o["status"] == status]
                if subset:
                    st.subheader(f"{ORDER_BADGE.get(status, status)} ({len(subset)})")
                    for o in subset:
                        _order_card(api, o, key, allow_cancel)
        else:
            for o in orders:
                _order_card(api, o, key, allow_cancel)

    board()


def page_orders(api) -> None:
    _title("orders")
    is_owner = api.ctx["role"] == "owner"
    tab_live, tab_hist = st.tabs(["Live orders", "History"])
    with tab_live:
        _live_orders(api, "orders", ACTIVE_ORDER_STATUSES, group=True, allow_cancel=is_owner)
    with tab_hist:
        start, end = date_filter("ord_hist", "Last 7 days")
        c1, c2 = st.columns([3, 2])
        statuses = c1.multiselect("Status", backend.ORDER_STATUSES, default=backend.ORDER_STATUSES,
                                  format_func=lambda s: ORDER_BADGE.get(s, s), key="ord_hist_status")
        search = c2.text_input("Search order # or table", key="ord_hist_search")
        mine = st.toggle("Only my orders", key="ord_hist_mine") if not is_owner else False
        ok, orders = attempt(api.list_orders, statuses or backend.ORDER_STATUSES, start, end,
                             search or None, mine, 500)
        if not ok:
            return
        for o in orders:
            o["status_label"] = ORDER_BADGE.get(o["status"], o["status"])
            o["refunded"] = "Yes" if o.get("refunded_at") else ""
        show_table(orders, {"order_number": "Order #", "table_label": "Table", "status_label": "Status",
                            "total": "Total", "waiter_name": "Waiter", "refunded": "Refunded",
                            "created_at": "Placed"}, money=("total",), dts=("created_at",),
                   empty="No orders match these filters.")
        if orders:
            by_id = {o["id"]: o for o in orders}
            pick = st.selectbox("View order details", list(by_id), key="ord_hist_pick",
                                format_func=lambda i: f"#{by_id[i]['order_number']} · Table {by_id[i].get('table_label')}")
            o = by_id[pick]
            with st.container(border=True):
                st.markdown(f"**#{o['order_number']}** · {ORDER_BADGE.get(o['status'])} · {_money(o['total'])}")
                for it in o.get("items", []):
                    st.markdown(f"- {it['quantity']} × {it['name']} ({_money(it['line_total'])})")
                if o.get("notes"):
                    st.info(f"📝 {o['notes']}")
                if o.get("cancel_reason"):
                    st.warning(f"Cancelled: {o['cancel_reason']}")
                if o.get("refunded_at"):
                    st.warning(f"Refunded on {fmt_dt(o['refunded_at'])}: {o.get('refund_reason') or ''}")
                elif is_owner and o["status"] == "completed":
                    with st.expander("Mark as fully refunded"):
                        st.caption("A refunded order is excluded from net sales and its commission is reversed.")
                        reason = st.text_input("Reason", key="refund_reason")
                        sure = st.checkbox("I confirm this order was refunded", key="refund_sure")
                        if st.button("Record refund", disabled=not sure):
                            if not reason.strip():
                                st.error("Please enter a reason.")
                            else:
                                ok, _ = attempt(api.refund_order, o["id"], reason.strip())
                                if ok:
                                    flash("Refund recorded.")
                                    st.rerun()


def page_kitchen(api) -> None:
    _title("kitchen", "Live view of orders being prepared.")
    _live_orders(api, "kitchen", KITCHEN_STATUSES, group=True, allow_cancel=api.ctx["role"] == "owner")


# ---------------------------------------------------------------------------
# CUSTOMER REQUESTS
# ---------------------------------------------------------------------------
def _request_card(api, r: dict, prefix: str) -> None:
    status = r["status"]
    with st.container(border=True):
        h1, h2 = st.columns([3, 2])
        h1.markdown(f"**{backend.REQUEST_TYPES.get(r['request_type'], r['request_type'])}** · "
                    f"Table **{r.get('table_label') or '?'}**")
        h2.markdown(REQUEST_BADGE.get(status, status))
        meta = f"Requested {fmt_dt(r['created_at'])}"
        if r.get("accepted_at"):
            meta += f" · Response time {fmt_duration(r['created_at'], r['accepted_at'])}"
        if r.get("completed_at"):
            meta += f" · Completed in {fmt_duration(r['created_at'], r['completed_at'])}"
        st.caption(meta)
        if r.get("escalated"):
            st.warning("⚠️ Escalated to the manager — not answered in time.")
        if r.get("note"):
            st.write(f"📝 {r['note']}")
        nexts = backend.REQUEST_TRANSITIONS.get(status, [])
        if nexts:
            cols = st.columns(len(nexts))
            for col, nxt in zip(cols, nexts):
                if col.button(REQUEST_BADGE.get(nxt, nxt), key=f"{prefix}_{r['id']}_{nxt}"):
                    ok, _ = attempt(api.update_request_status, r["id"], nxt)
                    if ok:
                        _rerun()


def page_requests(api) -> None:
    _title("requests")
    tab_live, tab_hist = st.tabs(["Live requests", "History"])
    with tab_live:
        c1, c2 = st.columns(2)
        auto = c1.toggle("Auto-refresh (10s)", value=True, key="req_auto")
        sound = c2.toggle("🔔 Sound alert", value=False, key="req_sound",
                          help="Your browser may need one tap on the page before it allows sound.")

        @st.fragment(run_every=10 if auto else None)
        def board() -> None:
            ok, rows = attempt(api.list_requests, ACTIVE_REQUEST_STATUSES, None, None)
            if not ok:
                return
            pending = sum(1 for r in rows if r["status"] == "pending")
            _alert_if_increased("req_seen", pending, "A customer needs assistance!", sound)
            st.caption(f"{len(rows)} open request(s) · updated {datetime.now(TZ):%I:%M:%S %p}")
            if not rows:
                st.info("No open requests.")
            for r in rows:
                _request_card(api, r, "req")

        board()
    with tab_hist:
        start, end = date_filter("req_hist", "Last 7 days")
        ok, rows = attempt(api.list_requests, backend.REQUEST_STATUSES, start, end)
        if ok:
            for r in rows:
                r["type_label"] = backend.REQUEST_TYPES.get(r["request_type"], r["request_type"])
                r["status_label"] = REQUEST_BADGE.get(r["status"], r["status"])
                r["response"] = fmt_duration(r["created_at"], r.get("accepted_at"))
                r["total_time"] = fmt_duration(r["created_at"], r.get("completed_at"))
                r["escalated_label"] = "Yes" if r.get("escalated") else ""
            show_table(rows, {"created_at": "Requested", "table_label": "Table", "type_label": "Type",
                              "status_label": "Status", "response": "Response time",
                              "total_time": "Completion time", "escalated_label": "Escalated"},
                       dts=("created_at",))


# ---------------------------------------------------------------------------
# OWNER pages
# ---------------------------------------------------------------------------
def page_dashboard(api) -> None:
    _title("dashboard")
    ok, s = attempt(api.dashboard_summary)
    if not ok:
        return
    kpi_row([
        ("Today's orders", str(s["today_orders"])),
        ("Today's revenue", _money(s["today_revenue"])),
        ("This week", _money(s["week_revenue"])),
        ("This month", _money(s["month_revenue"])),
        ("Open orders", str(s["pending_orders"])),
        ("Open requests", str(s["pending_requests"])),
        ("Low-stock items", str(s["low_stock_count"])),
        ("Unread messages", str(s["unread_messages"])),
    ])
    st.subheader("Recent orders")
    rows = s.get("recent_orders") or []
    for o in rows:
        o["status_label"] = ORDER_BADGE.get(o["status"], o["status"])
    show_table(rows, {"order_number": "Order #", "table_label": "Table", "status_label": "Status",
                      "total": "Total", "created_at": "Placed"}, money=("total",), dts=("created_at",),
               empty="No orders yet. Print your table QR codes to get started.")


def page_restaurant(api) -> None:
    _title("restaurant")
    ok, r = attempt(api.get_restaurant)
    if not ok:
        return
    if r.get("logo_url"):
        st.image(r["logo_url"], width=120)
    with st.form("profile_form"):
        name = st.text_input("Restaurant name *", value=r["name"])
        c1, c2 = st.columns(2)
        phone = c1.text_input("Phone", value=r.get("phone") or "")
        email = c2.text_input("Contact email", value=r.get("email") or "")
        address = st.text_input("Address", value=r.get("address") or "")
        city = st.text_input("City", value=r.get("city") or "")
        st.markdown("**Operations**")
        basis = st.radio("Waiter commission is calculated on", ["gross", "net"], horizontal=True,
                         index=0 if r.get("commission_basis", "gross") == "gross" else 1,
                         format_func=lambda b: "Gross sales" if b == "gross" else "Net sales (after discounts)")
        esc = st.number_input("Escalate unanswered customer requests to manager after (minutes)",
                              min_value=1, max_value=120, value=int(r.get("escalation_minutes") or 5))
        c3, c4 = st.columns(2)
        see_sales = c3.checkbox("Waiters can see sales summaries", value=bool(r.get("waiter_can_view_sales")))
        see_comm = c4.checkbox("Waiters can see their commission", value=bool(r.get("waiter_can_view_commission")))
        logo = st.file_uploader("Logo (PNG, JPG or WebP, max 2 MB)", type=["png", "jpg", "jpeg", "webp"])
        if st.form_submit_button("Save profile", type="primary"):
            if not name.strip():
                st.error("Restaurant name is required.")
            else:
                def _save():
                    fields = {"name": name.strip(), "phone": phone.strip(), "email": email.strip(),
                              "address": address.strip(), "city": city.strip(), "commission_basis": basis,
                              "escalation_minutes": int(esc), "waiter_can_view_sales": see_sales,
                              "waiter_can_view_commission": see_comm}
                    up = read_upload(logo, 2)
                    if up:
                        fields["logo_url"] = api.upload_logo(*up)
                    api.update_restaurant(fields)
                ok, _ = attempt(_save)
                if ok:
                    flash("Profile saved.")
                    st.rerun()


def _item_form(api, item: dict | None, categories: list[dict], key: str) -> None:
    item = item or {}
    ids = [c["id"] for c in categories]
    names = {c["id"]: c["name"] for c in categories}
    with st.form(key):
        name = st.text_input("Product name *", value=item.get("name", ""))
        cat = st.selectbox("Category *", ids, format_func=names.get,
                           index=ids.index(item["category_id"]) if item.get("category_id") in ids else 0)
        c1, c2 = st.columns(2)
        price = c1.number_input("Price (Rs.) *", min_value=0.0, step=10.0, format="%.2f",
                                value=float(item.get("price") or 0))
        cost = c2.number_input("Cost price (Rs., optional — used for profit)", min_value=0.0, step=10.0,
                               format="%.2f", value=float(item.get("cost_price") or 0))
        desc = st.text_area("Description", value=item.get("description") or "")
        c3, c4, c5 = st.columns(3)
        avail = c3.checkbox("Available now", value=bool(item.get("is_available", True)))
        feat = c4.checkbox("Featured", value=bool(item.get("is_featured")))
        pop = c5.checkbox("Popular", value=bool(item.get("is_popular")))
        if item.get("image_url"):
            st.image(item["image_url"], width=140)
        img = st.file_uploader("Image (PNG, JPG or WebP, max 3 MB)", type=["png", "jpg", "jpeg", "webp"],
                               key=f"{key}_img")
        if st.form_submit_button("Save product", type="primary"):
            if not name.strip():
                st.error("Product name is required.")
            elif price <= 0:
                st.error("Price must be greater than zero.")
            else:
                def _save():
                    payload = {"id": item.get("id"), "name": name.strip(), "category_id": cat,
                               "price": price, "cost_price": cost or None, "description": desc.strip(),
                               "is_available": avail, "is_featured": feat, "is_popular": pop,
                               "image_url": item.get("image_url")}
                    up = read_upload(img, 3)
                    if up:
                        payload["image_url"] = api.upload_menu_image(*up)
                    api.save_menu_item(payload)
                ok, _ = attempt(_save)
                if ok:
                    flash("Product saved.")
                    st.rerun()


def page_menu(api) -> None:
    _title("menu", "Customers always see the current price. Orders are priced by the server.")
    ok, cats = attempt(api.list_categories)
    if not ok:
        return
    tab_items, tab_add, tab_cats = st.tabs(["Products", "Add product", "Categories"])

    with tab_cats:
        with st.form("new_cat"):
            c1, c2 = st.columns([3, 1])
            name = c1.text_input("New category name")
            order = c2.number_input("Order", min_value=0, value=len(cats) * 10, step=1)
            if st.form_submit_button("Add category", type="primary"):
                if not name.strip():
                    st.error("Category name is required.")
                else:
                    ok, _ = attempt(api.save_category, None, name.strip(), int(order), True)
                    if ok:
                        flash("Category added.")
                        st.rerun()
        for c in cats:
            with st.expander(f"{c['name']}{'' if c.get('is_active', True) else ' (hidden)'}"):
                with st.form(f"cat_{c['id']}"):
                    name = st.text_input("Name", value=c["name"])
                    order = st.number_input("Display order", min_value=0, value=int(c.get("sort_order") or 0))
                    active = st.checkbox("Visible to customers", value=bool(c.get("is_active", True)))
                    if st.form_submit_button("Save", type="primary"):
                        if not name.strip():
                            st.error("Name is required.")
                        else:
                            ok, _ = attempt(api.save_category, c["id"], name.strip(), int(order), active)
                            if ok:
                                flash("Category saved.")
                                st.rerun()

    with tab_add:
        if not cats:
            st.info("Create a category first (Categories tab).")
        else:
            _item_form(api, None, cats, "new_item")

    with tab_items:
        ok, items = attempt(api.list_menu_items)
        if not ok:
            return
        c1, c2, c3 = st.columns([3, 2, 2])
        q = c1.text_input("Search", key="menu_q")
        cat_names = {c["id"]: c["name"] for c in cats}
        cat_filter = c2.selectbox("Category", ["ALL"] + list(cat_names),
                                  format_func=lambda i: "All categories" if i == "ALL" else cat_names[i])
        show_inactive = c3.checkbox("Show deactivated", value=False)
        shown = [i for i in items
                 if (q.lower() in i["name"].lower())
                 and (cat_filter == "ALL" or i["category_id"] == cat_filter)
                 and (show_inactive or i.get("is_active", True))]
        if not shown:
            st.info("No products found.")
        for i in shown:
            flags = ("⭐ " if i.get("is_featured") else "") + ("🔥 " if i.get("is_popular") else "")
            state = "" if i.get("is_active", True) else " · DEACTIVATED"
            state += "" if i.get("is_available", True) else " · unavailable"
            with st.expander(f"{flags}{i['name']} — {_money(i['price'])}{state}"):
                _item_form(api, i, cats, f"item_{i['id']}")
                if i.get("is_active", True):
                    sure = st.checkbox("Confirm: hide this product from the menu", key=f"deact_c_{i['id']}")
                    if st.button("Deactivate product", key=f"deact_{i['id']}", disabled=not sure):
                        ok, _ = attempt(api.set_menu_item_active, i["id"], False)
                        if ok:
                            flash("Product deactivated.", "warning")
                            st.rerun()
                else:
                    if st.button("Reactivate product", key=f"react_{i['id']}"):
                        ok, _ = attempt(api.set_menu_item_active, i["id"], True)
                        if ok:
                            flash("Product reactivated.")
                            st.rerun()


def page_tables(api) -> None:
    _title("tables", "Each table has its own secret QR link. Customers scan with the normal phone camera.")
    base = api.customer_base_url()
    if not base:
        st.warning("CUSTOMER_APP_URL is not set in your secrets, so QR codes cannot point to the customer "
                   "ordering page yet. See the README (Part 4) to deploy it.")
    with st.form("new_table"):
        c1, c2 = st.columns([3, 1])
        label = c1.text_input("Table name or number (e.g. 5 or Terrace-2)")
        seats = c2.number_input("Seats", min_value=1, max_value=50, value=4)
        if st.form_submit_button("Add table", type="primary"):
            if not label.strip():
                st.error("Please enter a table name.")
            else:
                ok, _ = attempt(api.create_table, label.strip(), int(seats))
                if ok:
                    flash("Table added.")
                    st.rerun()
    ok, tables = attempt(api.list_tables)
    if not ok:
        return
    if not tables:
        st.info("No tables yet. Add your first table above.")
    for tb in tables:
        with st.container(border=True):
            c1, c2 = st.columns([1, 2])
            url = api.build_table_url(tb["token"]) if base else None
            with c1:
                if url:
                    png = api.make_qr_png(url)
                    st.image(png, width=170)
                    st.download_button("⬇️ Download QR", png, file_name=f"table-{tb['label']}-qr.png",
                                       mime="image/png", key=f"dl_{tb['id']}")
            with c2:
                st.markdown(f"### Table {tb['label']} {'' if tb.get('is_active', True) else '(inactive)'}")
                st.caption(f"{tb.get('seats', '')} seats")
                if url:
                    st.code(url, language=None)
                a, b = st.columns(2)
                if tb.get("is_active", True):
                    if a.button("Deactivate", key=f"td_{tb['id']}"):
                        ok, _ = attempt(api.set_table_active, tb["id"], False)
                        if ok:
                            st.rerun()
                else:
                    if a.button("Activate", key=f"ta_{tb['id']}"):
                        ok, _ = attempt(api.set_table_active, tb["id"], True)
                        if ok:
                            st.rerun()
                with b.popover("Regenerate QR"):
                    st.warning("The old QR code for this table will stop working immediately. "
                               "Use this only if the QR was leaked or misused.")
                    sure = st.checkbox("I understand", key=f"rg_c_{tb['id']}")
                    if st.button("Regenerate", key=f"rg_{tb['id']}", disabled=not sure):
                        ok, _ = attempt(api.regenerate_table_token, tb["id"])
                        if ok:
                            flash("New QR generated. Print and replace the old one.", "warning")
                            st.rerun()


def page_staff(api) -> None:
    _title("staff")
    tab_list, tab_new = st.tabs(["Waiters", "Add waiter"])
    with tab_new:
        with st.form("new_waiter"):
            name = st.text_input("Full name *")
            email = st.text_input("Email *")
            phone = st.text_input("Phone")
            pw = st.text_input("Temporary password *", type="password",
                               help="Give it to the waiter privately. They can change it after signing in.")
            pct = st.number_input("Commission (%)", min_value=0.0, max_value=100.0, step=0.5, value=0.0)
            if st.form_submit_button("Create waiter account", type="primary"):
                problem = ("Name is required." if not name.strip()
                           else "Enter a valid email." if "@" not in email else valid_password(pw))
                if problem:
                    st.error(problem)
                else:
                    ok, _ = attempt(api.create_employee, email.strip(), name.strip(), pw,
                                    phone.strip(), float(pct))
                    if ok:
                        flash("Waiter account created.")
                        st.rerun()
    with tab_list:
        ok, emps = attempt(api.list_employees)
        if not ok:
            return
        if not emps:
            st.info("No waiters yet.")
        for e in emps:
            tag = "" if e.get("is_active", True) else " (inactive)"
            with st.expander(f"{e['full_name']}{tag} — {e.get('commission_pct', 0)}% commission"):
                st.caption(e.get("email") or "")
                with st.form(f"emp_{e['id']}"):
                    name = st.text_input("Full name", value=e["full_name"])
                    phone = st.text_input("Phone", value=e.get("phone") or "")
                    pct = st.number_input("Commission (%)", min_value=0.0, max_value=100.0, step=0.5,
                                          value=float(e.get("commission_pct") or 0))
                    active = st.checkbox("Account active", value=bool(e.get("is_active", True)))
                    if st.form_submit_button("Save", type="primary"):
                        if not name.strip():
                            st.error("Name is required.")
                        else:
                            ok, _ = attempt(api.update_employee, e["id"], name.strip(), phone.strip(),
                                            float(pct), active)
                            if ok:
                                flash("Waiter updated.")
                                st.rerun()
                if e.get("email") and st.button("Send password reset email", key=f"pwr_{e['id']}"):
                    ok, _ = attempt(api.send_password_reset, e["email"])
                    if ok:
                        st.success("Reset email sent.")


def page_commission(api) -> None:
    _title("commission")
    st.info("**How commission works:** only *completed* orders earn commission. Cancelled orders earn "
            "nothing. If a completed order is later marked refunded, its commission is reversed. Each order "
            "is counted once. The base (gross or net sales) is set in Restaurant Profile.")
    start, end = date_filter("comm", "This month")
    ok, emps = attempt(api.list_employees)
    if not ok:
        return
    by_id = {e["id"]: e["full_name"] for e in emps}
    wid = st.selectbox("Waiter", ["ALL"] + list(by_id),
                       format_func=lambda i: "All waiters" if i == "ALL" else by_id[i])
    wid = None if wid == "ALL" else wid
    tab_sum, tab_hist = st.tabs(["Summary", "History"])
    with tab_sum:
        ok, rep = attempt(api.commission_report, start, end, wid)
        if ok:
            st.caption(f"Commission base: **{'Gross sales' if rep['basis'] == 'gross' else 'Net sales'}**")
            tot = rep["totals"]
            kpi_row([("Eligible sales", _money(tot["eligible_sales"])), ("Orders", str(tot["orders"])),
                     ("Commission earned", _money(tot["commission"]))])
            show_table(rep["rows"], {"waiter": "Waiter", "commission_pct": "Rate %", "orders": "Orders",
                                     "eligible_sales": "Eligible sales", "commission": "Commission"},
                       money=("eligible_sales", "commission"), empty="No eligible sales in this period.")
    with tab_hist:
        ok, rows = attempt(api.commission_history, start, end, wid)
        if ok:
            show_table(rows, {"completed_at": "Completed", "order_number": "Order #", "waiter": "Waiter",
                              "base_amount": "Base amount", "commission_pct": "Rate %",
                              "commission": "Commission", "status": "Status"},
                       money=("base_amount", "commission"), dts=("completed_at",))


def page_inventory(api) -> None:
    _title("inventory", "Stock is tracked manually. Automatic deduction from orders is NOT part of this version.")
    ok, items = attempt(api.list_inventory)
    if not ok:
        return
    low = [i for i in items if i["quantity"] <= i["reorder_threshold"]]
    if low:
        st.warning("⚠️ Low stock: " + ", ".join(f"{i['name']} ({i['quantity']} {i['unit']})" for i in low))
    tab_stock, tab_adjust, tab_new, tab_move = st.tabs(["Stock", "Add / remove stock", "New item", "Movements"])
    with tab_stock:
        for i in items:
            i["state"] = "🔴 Low" if i["quantity"] <= i["reorder_threshold"] else "🟢 OK"
        show_table(items, {"name": "Item", "quantity": "Quantity", "unit": "Unit",
                           "reorder_threshold": "Reorder at", "cost_per_unit": "Cost / unit", "state": "Level"},
                   money=("cost_per_unit",), empty="No inventory items yet.")
        if items:
            df = pd.DataFrame(items)[["name", "quantity", "unit", "reorder_threshold", "cost_per_unit"]]
            st.download_button("⬇️ Download inventory report (CSV)", df.to_csv(index=False).encode("utf-8"),
                               file_name=f"inventory-{today()}.csv", mime="text/csv")
            by_id = {i["id"]: i for i in items}
            pick = st.selectbox("Edit item", list(by_id), format_func=lambda x: by_id[x]["name"], key="inv_edit")
            _inventory_form(api, by_id[pick], f"inv_edit_{pick}")
    with tab_adjust:
        if not items:
            st.info("Create an inventory item first.")
        else:
            by_id = {i["id"]: i for i in items}
            with st.form("adjust_form"):
                pick = st.selectbox("Item", list(by_id), format_func=lambda x: f"{by_id[x]['name']} ({by_id[x]['quantity']} {by_id[x]['unit']})")
                kind = st.radio("Action", ["addition", "deduction"], horizontal=True,
                                format_func=lambda k: "Add stock" if k == "addition" else "Remove stock")
                qty = st.number_input("Quantity", min_value=0.0, step=1.0, format="%.2f")
                reason = st.text_input("Reason (e.g. purchase, wastage)")
                if st.form_submit_button("Apply", type="primary"):
                    if qty <= 0:
                        st.error("Quantity must be greater than zero.")
                    else:
                        ok, _ = attempt(api.adjust_stock, pick, kind, float(qty), reason.strip())
                        if ok:
                            flash("Stock updated.")
                            st.rerun()
    with tab_new:
        _inventory_form(api, None, "inv_new")
    with tab_move:
        ok, moves = attempt(api.list_stock_movements, None, 200)
        if ok:
            show_table(moves, {"created_at": "When", "item_name": "Item", "kind": "Type",
                               "quantity": "Quantity", "reason": "Reason"}, dts=("created_at",))


def _inventory_form(api, item: dict | None, key: str) -> None:
    item = item or {}
    with st.form(key):
        name = st.text_input("Item name *", value=item.get("name", ""))
        c1, c2, c3 = st.columns(3)
        unit = c1.text_input("Unit * (kg, litre, pcs)", value=item.get("unit", ""))
        thr = c2.number_input("Reorder threshold", min_value=0.0, step=1.0, format="%.2f",
                              value=float(item.get("reorder_threshold") or 0))
        cost = c3.number_input("Cost per unit (Rs.)", min_value=0.0, step=1.0, format="%.2f",
                               value=float(item.get("cost_per_unit") or 0))
        qty = None
        if not item:
            qty = st.number_input("Opening quantity", min_value=0.0, step=1.0, format="%.2f")
        if st.form_submit_button("Save item", type="primary"):
            if not name.strip() or not unit.strip():
                st.error("Name and unit are required.")
            else:
                payload = {"id": item.get("id"), "name": name.strip(), "unit": unit.strip(),
                           "reorder_threshold": float(thr), "cost_per_unit": float(cost)}
                if qty is not None:
                    payload["quantity"] = float(qty)
                ok, _ = attempt(api.save_inventory_item, payload)
                if ok:
                    flash("Inventory item saved.")
                    st.rerun()


def page_expenses(api) -> None:
    _title("expenses")
    with st.form("expense_form"):
        c1, c2 = st.columns(2)
        d = c1.date_input("Date", value=today())
        cat = c2.selectbox("Category", ["Ingredients", "Salaries", "Rent", "Utilities", "Maintenance",
                                        "Marketing", "Other"])
        desc = st.text_input("Description")
        amount = st.number_input("Amount (Rs.)", min_value=0.0, step=100.0, format="%.2f")
        if st.form_submit_button("Add expense", type="primary"):
            if amount <= 0:
                st.error("Amount must be greater than zero.")
            else:
                ok, _ = attempt(api.add_expense, d, cat, desc.strip(), float(amount))
                if ok:
                    flash("Expense added.")
                    st.rerun()
    st.subheader("Expense records")
    start, end = date_filter("exp", "This month")
    ok, rows = attempt(api.list_expenses, start, end)
    if not ok:
        return
    total = sum(float(r["amount"]) for r in rows)
    st.metric("Total expenses", _money(total))
    if rows:
        df = pd.DataFrame(rows)
        st.bar_chart(df.groupby("category")["amount"].sum())
    show_table(rows, {"expense_date": "Date", "category": "Category", "description": "Description",
                      "amount": "Amount"}, money=("amount",))
    if rows:
        by_id = {r["id"]: r for r in rows}
        pick = st.selectbox("Delete an expense", list(by_id), key="exp_del",
                            format_func=lambda i: f"{by_id[i]['expense_date']} · {by_id[i]['category']} · {_money(by_id[i]['amount'])}")
        sure = st.checkbox("Confirm deletion", key="exp_del_c")
        if st.button("Delete expense", disabled=not sure):
            ok, _ = attempt(api.delete_expense, pick)
            if ok:
                flash("Expense deleted.", "warning")
                st.rerun()


def page_analytics(api) -> None:
    _title("analytics", "Revenue is money received from sales. Profit is only estimated when cost data exists.")
    start, end = date_filter("ana", "Last 7 days")
    ok, a = attempt(api.analytics, start, end)
    if not ok:
        return
    k = a["kpis"]
    profit = k.get("estimated_profit")
    kpi_row([
        ("Orders", str(k["orders"])), ("Completed", str(k["completed"])), ("Cancelled", str(k["cancelled"])),
        ("Revenue", _money(k["revenue"])), ("Avg order value", _money(k["avg_order_value"])),
        ("Expenses", _money(k["expenses"])),
        ("Estimated profit", _money(profit) if profit is not None else "Not enough data"),
    ])
    if a.get("profit_note"):
        st.caption(f"ℹ️ {a['profit_note']}")
    st.subheader("Revenue by day")
    daily = a.get("daily") or []
    if daily:
        df = pd.DataFrame(daily).set_index("date")
        st.line_chart(df["revenue"])
        st.download_button("⬇️ Download daily report (CSV)", df.reset_index().to_csv(index=False).encode("utf-8"),
                           file_name=f"sales-{start}-{end}.csv", mime="text/csv")
    else:
        st.info("No sales in this period.")
    c1, c2 = st.columns(2)
    with c1:
        st.subheader("Popular items")
        top = a.get("top_items") or []
        if top:
            st.bar_chart(pd.DataFrame(top).set_index("name")["quantity"])
        else:
            st.info("No data.")
    with c2:
        st.subheader("Waiter-wise sales")
        show_table(a.get("waiter_sales"), {"waiter": "Waiter", "orders": "Orders", "sales": "Sales"},
                   money=("sales",), empty="No waiter sales.")
    low = a.get("low_stock") or []
    if low:
        st.warning("Inventory alerts: " + ", ".join(i["name"] for i in low))


def page_subscription(api) -> None:
    _title("subscription")
    ok, sub = attempt(api.get_my_subscription)
    if not ok:
        return
    kpi_row([("Plan", sub.get("plan_name") or "—"), ("Status", str(sub.get("status", "—")).title()),
             ("Expires", str(sub.get("expiry_date") or "—")),
             ("Days left", str(sub["days_left"]) if sub.get("days_left") is not None else "—")])
    st.subheader("Available plans")
    ok, plans = attempt(api.list_plans)
    plans = plans or []
    show_table(plans, {"name": "Plan", "price_pkr": "Price", "duration_days": "Days", "description": "Includes"},
               money=("price_pkr",))
    st.subheader("Submit payment proof")
    st.caption("After paying, upload your receipt. Your payment is reviewed by the platform administrator — "
               "uploading a receipt does not activate or renew your plan by itself.")
    paid = [p for p in plans if float(p.get("price_pkr") or 0) > 0 and p.get("is_active", True)]
    if not paid:
        st.info("No paid plans are available yet.")
    else:
        by_code = {p["code"]: p for p in paid}
        with st.form("proof_form"):
            code = st.selectbox("Plan", list(by_code), format_func=lambda c: f"{by_code[c]['name']} — {_money(by_code[c]['price_pkr'])}")
            amount = st.number_input("Amount paid (Rs.)", min_value=0.0, step=100.0, format="%.2f")
            ref = st.text_input("Transaction / reference number")
            note = st.text_area("Note (optional)")
            file = st.file_uploader("Receipt (PNG, JPG or PDF, max 5 MB)", type=["png", "jpg", "jpeg", "pdf"])
            if st.form_submit_button("Submit for review", type="primary"):
                if amount <= 0:
                    st.error("Enter the amount you paid.")
                elif file is None:
                    st.error("Please attach your receipt.")
                else:
                    def _send():
                        up = read_upload(file, 5)
                        api.submit_payment_proof(code, float(amount), ref.strip(), note.strip(), *up)
                    ok, _ = attempt(_send)
                    if ok:
                        flash("Payment proof submitted. You will be notified after review.")
                        st.rerun()
    st.subheader("Payment history")
    ok, rows = attempt(api.list_my_payments)
    if ok:
        show_table(rows, {"created_at": "Submitted", "plan_name": "Plan", "amount": "Amount",
                          "status": "Status", "rejection_reason": "Reviewer note"},
                   money=("amount",), dts=("created_at",))


def page_messages(api) -> None:
    _title("messages")
    ok, msgs = attempt(api.list_messages)
    if not ok:
        return
    if not msgs:
        st.info("No messages.")
    for m in msgs:
        with st.container(border=True):
            st.markdown(f"{'🔵 ' if not m.get('is_read') else ''}**{m['title']}**")
            st.caption(fmt_dt(m["created_at"]))
            st.write(m["body"])
            if not m.get("is_read") and st.button("Mark as read", key=f"read_{m['id']}"):
                ok, _ = attempt(api.mark_message_read, m["id"])
                if ok:
                    st.rerun()


# ---------------------------------------------------------------------------
# WAITER pages
# ---------------------------------------------------------------------------
def page_my_stats(api) -> None:
    _title("my_stats")
    start, end = date_filter("mystats", "Today")
    ok, s = attempt(api.waiter_stats, start, end)
    if not ok:
        return
    items = [("Orders handled", str(s["orders"])), ("Completed", str(s["completed"])),
             ("Cancelled", str(s["cancelled"])), ("Requests handled", str(s.get("requests_handled", 0)))]
    if s.get("sales_visible"):
        items.append(("My sales", _money(s["sales"])))
    if s.get("commission_visible"):
        items.append(("My commission", _money(s["commission"])))
    kpi_row(items)
    if not s.get("sales_visible"):
        st.caption("Sales and commission figures are shown only if your restaurant owner enables them.")


# ---------------------------------------------------------------------------
# Routing table
# ---------------------------------------------------------------------------
PAGES = {
    "admin_overview": page_admin_overview, "admin_restaurants": page_admin_restaurants,
    "admin_plans": page_admin_plans, "admin_subscriptions": page_admin_subscriptions,
    "admin_payments": page_admin_payments, "admin_messages": page_admin_messages,
    "admin_settings": page_admin_settings, "admin_ai": page_admin_ai, "admin_audit": page_admin_audit,
    "dashboard": page_dashboard, "orders": page_orders, "kitchen": page_kitchen,
    "requests": page_requests, "menu": page_menu, "tables": page_tables, "staff": page_staff,
    "commission": page_commission, "inventory": page_inventory, "expenses": page_expenses,
    "analytics": page_analytics, "restaurant": page_restaurant,
    "subscription": page_subscription, "messages": page_messages,
    "my_stats": page_my_stats, "account": page_account,
}
