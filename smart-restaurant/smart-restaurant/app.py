"""
app.py - Smart Restaurant dashboard entry point.

Responsibilities:
  * page configuration and theme
  * configuration check (Supabase secrets present?)
  * authentication flow (login, password recovery, sign out)
  * session handling (one backend API object per browser session)
  * navigation and routing (role-based page list, subscription gate)

All UI lives in frontend.py. All data access, validation and authorization
helpers live in backend.py. Real security is enforced by Supabase Row Level
Security and database functions; hiding pages here is only a convenience.

Run locally:   streamlit run app.py
"""
from __future__ import annotations

import logging

import streamlit as st

import backend
import frontend

logger = logging.getLogger("smart_restaurant")

st.set_page_config(
    page_title="Smart Restaurant",
    page_icon="🍽️",
    layout="wide",
    initial_sidebar_state="auto",
)


def get_api() -> "backend.SmartRestaurantAPI":
    """Return this browser session's backend API object (created once)."""
    if "api" not in st.session_state:
        st.session_state["api"] = backend.SmartRestaurantAPI()
    return st.session_state["api"]


def reset_session() -> None:
    """Forget everything about the current user (used when a session expires)."""
    for key in list(st.session_state.keys()):
        del st.session_state[key]


def main() -> None:
    frontend.inject_theme()

    # 1. Configuration check -------------------------------------------------
    if not backend.is_configured():
        frontend.render_setup_help(backend.config_help())
        return

    api = get_api()
    frontend.load_labels(api)

    # 2. Password recovery link (Supabase email -> ?token_hash=...&type=recovery)
    params = st.query_params
    if params.get("token_hash") and params.get("type") == "recovery":
        frontend.render_recovery(api, params["token_hash"])
        return

    # 3. Authentication ------------------------------------------------------
    if not api.is_authenticated:
        frontend.render_login(api)
        return

    ctx = api.ctx
    if not ctx.get("role"):
        frontend.render_no_access(api)
        return

    # 4. Navigation + routing ------------------------------------------------
    try:
        gate = api.get_access_gate()
        pages = frontend.allowed_pages(ctx["role"], gate)
        page = frontend.render_sidebar(api, pages, gate)
        frontend.render_announcement(api)
        frontend.render_subscription_banner(gate)
        frontend.render_page(api, page)
    except backend.SessionExpired:
        reset_session()
        frontend.flash("Your session expired. Please sign in again.", "warning")
        st.rerun()
    except backend.AppError as exc:
        st.error(str(exc))
    except Exception:  # pragma: no cover - last-resort safety net
        logger.exception("Unhandled error while rendering page")
        st.error(
            "Something went wrong. Please refresh the page and try again. "
            "If the problem continues, contact support."
        )


main()
