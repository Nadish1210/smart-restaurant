"""
backend.py - Smart Restaurant business logic and Supabase integration.

This module is the ONLY place that talks to Supabase. The Streamlit UI
(frontend.py) calls `SmartRestaurantAPI` methods and never touches the
database directly.

SECURITY MODEL
  * The dashboard only ever holds the public ANON key plus the signed-in
    user's own JWT. Every query therefore runs under Supabase Row Level
    Security (RLS) as that user. The service-role key is never used here and
    is actively refused if someone pastes it into the secrets by mistake.
  * Privileged actions that need the service-role key (creating Auth users)
    are done by the `admin-users` Supabase Edge Function (Part 4), which
    verifies the caller's role itself.
  * The AI API key lives only in the Supabase Edge Function secrets
    (`ai-assistant` function, Part 4). This module never sees it.
  * Validation here gives friendly messages early; the database (constraints,
    RPC functions, RLS) is the real authority and re-checks everything.

DATABASE CONTRACT (implemented by database.sql - Part 3)
  Tables : profiles, restaurants, restaurant_employees, subscription_plans,
           subscriptions, subscription_payments, restaurant_messages,
           menu_categories, menu_items, restaurant_tables, orders,
           order_items, waiter_requests, inventory, inventory_transactions,
           expenses, sales_records, system_settings, audit_logs
  RPCs   : get_my_context, get_public_settings, get_labels, get_access_gate,
           update_order_status, cancel_order, refund_order,
           update_request_status, escalate_stale_requests,
           regenerate_table_token, adjust_stock, mark_message_read,
           submit_payment_proof, dashboard_summary, analytics,
           commission_report, commission_history, waiter_stats,
           admin_overview, admin_list_restaurants, admin_update_restaurant,
           admin_set_restaurant_status, admin_save_plan,
           admin_set_subscription, admin_renew_subscription,
           admin_extend_subscription, admin_send_expiry_reminders,
           admin_review_payment, admin_send_message, admin_save_setting
  Edge Functions: admin-users (create_restaurant, assign_owner,
           create_employee), ai-assistant (status, test, recommend)
  Storage: public bucket `restaurant-assets` (logos, menu images, folder per
           restaurant id) and private bucket `payment-proofs`.
  Customer link format: <CUSTOMER_APP_URL>/?t=<table token>
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import time
import uuid
from datetime import date, datetime, time as dtime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from io import BytesIO
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

logger = logging.getLogger("smart_restaurant.backend")

APP_TZ = ZoneInfo("Asia/Karachi")

# ---------------------------------------------------------------------------
# Exceptions and constants
# ---------------------------------------------------------------------------


class AppError(Exception):
    """An error whose message is safe and helpful to show to the user."""


class SessionExpired(AppError):
    """The user's login is no longer valid; they must sign in again."""


ORDER_STATUSES = ["received", "confirmed", "preparing", "ready", "served", "completed", "cancelled"]
ORDER_TRANSITIONS = {
    "received": ["confirmed", "cancelled"],
    "confirmed": ["preparing", "cancelled"],
    "preparing": ["ready", "cancelled"],
    "ready": ["served", "cancelled"],
    "served": ["completed", "cancelled"],
    "completed": [],
    "cancelled": [],
}
REQUEST_STATUSES = ["pending", "accepted", "in_progress", "completed", "cancelled"]
REQUEST_TRANSITIONS = {
    "pending": ["accepted", "cancelled"],
    "accepted": ["in_progress", "completed", "cancelled"],
    "in_progress": ["completed", "cancelled"],
    "completed": [],
    "cancelled": [],
}
REQUEST_TYPES = {
    "call_waiter": "Call waiter",
    "bill": "Request bill",
    "water": "Request water",
    "cutlery": "Request cutlery",
    "assistance": "Request assistance",
    "other": "Other",
}
SUBSCRIPTION_STATUSES = ["trial", "active", "expired", "suspended"]
PAYMENT_STATUSES = ["pending", "approved", "rejected"]
RESTAURANT_STATUSES = ["active", "suspended"]
STOCK_KINDS = ["addition", "deduction"]
COMMISSION_BASES = ["gross", "net"]

AI_PROVIDERS = {"gemini": "Google Gemini"}
DEFAULT_AI_PROVIDER = "gemini"
DEFAULT_AI_MODEL = "gemini-2.5-flash"  # editable by the Super Admin; check Google AI Studio for current names

SETTING_KEYS = {
    "login_help_text", "forgot_password_text", "global_announcement",
    "subscription_reminder_text", "expiry_reminder_days", "ui_labels",
    "ai_provider", "ai_model", "ai_enabled",
}
PUBLIC_SETTING_DEFAULTS = {
    "login_help_text": "", "forgot_password_text": "",
    "global_announcement": "", "subscription_reminder_text": "",
}

ASSETS_BUCKET = "restaurant-assets"
PROOFS_BUCKET = "payment-proofs"

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
_HASH_RE = re.compile(r"^[A-Za-z0-9_-]{10,300}$")
_CODE_RE = re.compile(r"^[a-z0-9_]{2,40}$")


# ---------------------------------------------------------------------------
# Configuration (secrets)
# ---------------------------------------------------------------------------


def _secret(name: str, default: str = "") -> str:
    """Read a value from Streamlit secrets, then environment variables."""
    try:
        import streamlit as st  # imported lazily so tests run without Streamlit

        value = st.secrets.get(name)
        if value:
            return str(value).strip()
    except Exception:
        pass
    return os.environ.get(name, default).strip()


def _jwt_role(key: str) -> str | None:
    """Return the `role` claim of a Supabase JWT key (unverified), if readable."""
    try:
        payload = key.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload)).get("role")
    except Exception:
        return None


def _is_privileged_key(key: str) -> bool:
    return key.startswith("sb_secret_") or _jwt_role(key) == "service_role"


def _valid_url(url: str) -> bool:
    return url.startswith("https://") or url.startswith("http://localhost") or url.startswith("http://127.0.0.1")


def is_configured() -> bool:
    url, key = _secret("SUPABASE_URL"), _secret("SUPABASE_ANON_KEY")
    return bool(url and key and _valid_url(url) and not _is_privileged_key(key))


def config_help() -> str:
    url, key = _secret("SUPABASE_URL"), _secret("SUPABASE_ANON_KEY")
    if key and _is_privileged_key(key):
        return (
            "🚫 **The key you configured is a *service-role / secret* key.** "
            "That key bypasses all security and must never be used in this app. "
            "Remove it from your secrets and use the **anon (public) key** instead "
            "(Supabase → Project Settings → API)."
        )
    problems = []
    if not url or not _valid_url(url):
        problems.append("`SUPABASE_URL` is missing or not a valid https address")
    if not key:
        problems.append("`SUPABASE_ANON_KEY` is missing")
    return (
        "Add these to **Streamlit Cloud → your app → Settings → Secrets** "
        "(or to `.streamlit/secrets.toml` when running locally):\n\n"
        "```toml\nSUPABASE_URL = \"https://YOUR-PROJECT-REF.supabase.co\"\n"
        "SUPABASE_ANON_KEY = \"YOUR-ANON-PUBLIC-KEY\"\n"
        "CUSTOMER_APP_URL = \"https://your-customer-app-address\"\n```\n\n"
        + ("Problems found: " + "; ".join(problems) + "." if problems else "")
    )


# ---------------------------------------------------------------------------
# Pure helpers: formatting, validation, dates, files, QR
# ---------------------------------------------------------------------------


def format_pkr(amount: Any) -> str:
    """Format a number as Pakistani Rupees, e.g. 'Rs. 1,250' or 'Rs. 1,250.50'."""
    try:
        value = Decimal(str(amount if amount is not None else 0))
        if not value.is_finite():
            value = Decimal(0)
    except InvalidOperation:
        value = Decimal(0)
    value = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if value == value.to_integral_value():
        return f"Rs. {int(value):,}"
    return f"Rs. {value:,.2f}"


def clean_text(value: Any, label: str, max_len: int = 200, required: bool = False) -> str:
    text = "" if value is None else str(value)
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text).strip()
    if required and not text:
        raise AppError(f"{label} is required.")
    if len(text) > max_len:
        raise AppError(f"{label} must be at most {max_len} characters.")
    return text


def clean_email(value: Any) -> str:
    email = clean_text(value, "Email", 254, required=True).lower()
    if not _EMAIL_RE.match(email):
        raise AppError("Please enter a valid email address.")
    return email


def check_password(password: str) -> str:
    if not isinstance(password, str) or len(password) < 8:
        raise AppError("Password must be at least 8 characters.")
    if len(password) > 128:
        raise AppError("Password must be at most 128 characters.")
    if not any(c.isalpha() for c in password) or not any(c.isdigit() for c in password):
        raise AppError("Password must contain at least one letter and one number.")
    return password


def clean_amount(value: Any, label: str, min_value: float = 0.0, max_value: float = 100_000_000.0,
                 allow_zero: bool = True) -> float:
    try:
        number = Decimal(str(value))
        if not number.is_finite():
            raise InvalidOperation
    except (InvalidOperation, ValueError):
        raise AppError(f"{label} must be a number.") from None
    if number < Decimal(str(min_value)) or number > Decimal(str(max_value)):
        raise AppError(f"{label} is out of the allowed range.")
    if number == 0 and not allow_zero:
        raise AppError(f"{label} must be greater than zero.")
    return float(number.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def clean_int(value: Any, label: str, min_value: int, max_value: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise AppError(f"{label} must be a whole number.") from None
    if not min_value <= number <= max_value:
        raise AppError(f"{label} must be between {min_value} and {max_value}.")
    return number


def clean_uuid(value: Any, label: str = "Record") -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, AttributeError, TypeError):
        raise AppError(f"{label} is not valid.") from None


def clean_code(value: Any, label: str = "Code") -> str:
    code = str(value or "").strip().lower()
    if not _CODE_RE.match(code):
        raise AppError(f"{label} is not valid.")
    return code


def as_iso_date(value: Any, label: str = "Date") -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    try:
        return date.fromisoformat(str(value)).isoformat()
    except ValueError:
        raise AppError(f"{label} is not a valid date.") from None


def today_local() -> date:
    return datetime.now(APP_TZ).date()


def _utc_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def day_bounds(start: Any, end: Any) -> tuple[str, str]:
    """UTC timestamps covering [start 00:00, end+1 day 00:00) in Pakistan time."""
    s, e = date.fromisoformat(as_iso_date(start, "Start date")), date.fromisoformat(as_iso_date(end, "End date"))
    if s > e:
        raise AppError("The start date must not be after the end date.")
    lo = datetime.combine(s, dtime.min, tzinfo=APP_TZ)
    hi = datetime.combine(e + timedelta(days=1), dtime.min, tzinfo=APP_TZ)
    return _utc_iso(lo), _utc_iso(hi)


def _like(text: str) -> str:
    """Make user search text safe to embed in a PostgREST filter."""
    return re.sub(r"[%*,()\\\"'.:;]", " ", text).strip()[:60]


_MAGIC = (
    ("png", b"\x89PNG\r\n\x1a\n"),
    ("jpg", b"\xff\xd8\xff"),
    ("pdf", b"%PDF-"),
)
_MIME = {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp", "pdf": "application/pdf"}
_EXT = {"png": "png", "jpg": "jpg", "webp": "webp", "pdf": "pdf"}


def detect_file_kind(data: bytes) -> str | None:
    """Identify a file by its real content (not by its name or browser-supplied type)."""
    for kind, magic in _MAGIC:
        if data.startswith(magic):
            return kind
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def validate_upload(data: bytes, allowed: set[str], max_mb: int) -> tuple[str, str, str]:
    """Return (kind, extension, mime) or raise AppError. Checks size and real file type."""
    if not data:
        raise AppError("The file is empty.")
    if len(data) > max_mb * 1024 * 1024:
        raise AppError(f"The file is too large (maximum {max_mb} MB).")
    kind = detect_file_kind(data)
    if kind not in allowed:
        names = ", ".join(sorted(k.upper() for k in allowed))
        raise AppError(f"Unsupported file type. Allowed: {names}.")
    return kind, _EXT[kind], _MIME[kind]


def make_qr_png(url: str) -> bytes:
    """Render a QR code PNG for `url`."""
    if not (url.startswith("https://") or url.startswith("http://localhost")):
        raise AppError("QR codes can only be created for https links.")
    try:
        import qrcode
        from qrcode.constants import ERROR_CORRECT_M
    except ImportError:
        raise AppError("QR support is not installed (package 'qrcode[pil]').") from None
    qr = qrcode.QRCode(version=None, error_correction=ERROR_CORRECT_M, box_size=10, border=4)
    qr.add_data(url)
    qr.make(fit=True)
    image = qr.make_image(fill_color="black", back_color="white")
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _with_defaults(data: Any, defaults: dict) -> dict:
    result = dict(defaults)
    if isinstance(data, dict):
        result.update(data)
    return result


def _embedded_name(row: dict, key: str) -> str | None:
    value = row.pop(key, None)
    if isinstance(value, list):
        value = value[0] if value else None
    return value.get("name") if isinstance(value, dict) else None


# ---------------------------------------------------------------------------
# The API object (one per browser session)
# ---------------------------------------------------------------------------


class SmartRestaurantAPI:
    """All data access for one signed-in (or signed-out) browser session."""

    def __init__(self) -> None:
        self.url = _secret("SUPABASE_URL").rstrip("/")
        self.anon_key = _secret("SUPABASE_ANON_KEY")
        if not (self.url and self.anon_key) or _is_privileged_key(self.anon_key):
            raise AppError("The application is not configured correctly.")
        self._cache: dict[str, tuple[float, Any]] = {}
        self._reset_auth_state()
        self.client = self._new_client()

    # -- client / session ---------------------------------------------------
    def _new_client(self):
        from supabase import create_client

        try:
            try:
                from supabase.lib.client_options import SyncClientOptions as Options
            except ImportError:
                from supabase.lib.client_options import ClientOptions as Options
            options = Options(auto_refresh_token=False, persist_session=False)
            return create_client(self.url, self.anon_key, options=options)
        except Exception:
            return create_client(self.url, self.anon_key)

    def _reset_auth_state(self) -> None:
        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self._expires_at = 0.0
        self._ctx: dict = {}
        self._cache = {}

    def _hard_reset(self) -> None:
        self._reset_auth_state()
        try:
            self.client = self._new_client()
        except Exception:
            pass

    def _store_session(self, session: Any) -> None:
        self._access_token = session.access_token
        self._refresh_token = session.refresh_token
        self._expires_at = float(getattr(session, "expires_at", None) or (time.time() + 3000))
        try:
            self.client.postgrest.auth(self._access_token)
        except Exception:
            logger.warning("Could not attach the access token to the data client.")

    def _ensure_session(self) -> None:
        if not self._access_token or time.time() < self._expires_at - 60:
            return
        try:
            result = self.client.auth.refresh_session(self._refresh_token)
            self._store_session(result.session)
        except Exception:
            self._hard_reset()
            raise SessionExpired("Your session expired. Please sign in again.") from None

    @property
    def is_authenticated(self) -> bool:
        return bool(self._access_token and self._ctx.get("user_id"))

    @property
    def ctx(self) -> dict:
        return dict(self._ctx)

    def _need(self, *roles: str) -> None:
        """Friendly early check. The database enforces the real rule."""
        if self._ctx.get("role") not in roles:
            raise AppError("You do not have permission to do that.")

    def _rid(self) -> str:
        rid = self._ctx.get("restaurant_id")
        if not rid:
            raise AppError("Your account is not linked to a restaurant.")
        return rid

    # -- low-level data access ---------------------------------------------
    def _table(self, name: str):
        self._ensure_session()
        return self.client.table(name)

    def _rpc(self, name: str, params: dict | None = None):
        self._ensure_session()
        return self.client.rpc(name, params or {})

    def _exec(self, builder):
        try:
            return builder.execute()
        except Exception as exc:
            raise self._translate(exc) from None

    def _call(self, name: str, params: dict | None = None):
        return self._exec(self._rpc(name, params)).data

    def _cached(self, key: str, ttl: float, loader):
        hit = self._cache.get(key)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
        value = loader()
        self._cache[key] = (time.time(), value)
        return value

    def _translate(self, exc: Exception) -> AppError:
        """Turn any library error into a safe, friendly AppError."""
        if isinstance(exc, AppError):
            return exc
        text = str(getattr(exc, "message", None) or exc)
        low = text.lower()
        code = str(getattr(exc, "code", "") or "")
        if "jwt expired" in low or code in ("PGRST301", "PGRST303"):
            self._hard_reset()
            return SessionExpired("Your session expired. Please sign in again.")
        if "invalid login credentials" in low:
            return AppError("Incorrect email or password.")
        if "email not confirmed" in low:
            return AppError("Please confirm your email address first.")
        if "rate limit" in low or "too many" in low or code == "over_request_rate_limit":
            return AppError("Too many attempts. Please wait a minute and try again.")
        if "same password" in low or "different from the old" in low:
            return AppError("The new password must be different from the old one.")
        if "weak" in low and "password" in low:
            return AppError("That password is too weak. Choose a stronger one.")
        if "expired or is invalid" in low or "otp_expired" in low or "invalid or has expired" in low:
            return AppError("This link is invalid or has expired.")
        if code == "P0001":  # our own RAISE EXCEPTION messages are written for users
            return AppError(text[:300])
        if code in ("42501", "PGRST302") or "row-level security" in low or "permission denied" in low:
            return AppError("You do not have permission to do that.")
        if code == "23505":
            return AppError("A record with the same name or value already exists.")
        if code == "23503":
            return AppError("This record is linked to other data and cannot be changed that way.")
        if code in ("23514", "22P02", "22003", "22007"):
            return AppError("One of the values is not allowed. Please check your input.")
        if any(w in low for w in ("connect", "timeout", "timed out", "network", "name resolution")):
            return AppError("Cannot reach the server. Check your connection and try again.")
        logger.warning("Unexpected backend error: %s code=%s text=%s", type(exc).__name__, code, text[:200])
        return AppError("Something went wrong while talking to the server. Please try again.")

    # -- HTTP helpers (Edge Functions and Storage) --------------------------
    def _headers(self, content_type: str | None = "application/json") -> dict:
        if not self._access_token:
            raise SessionExpired("Please sign in again.")
        headers = {"Authorization": f"Bearer {self._access_token}", "apikey": self.anon_key}
        if content_type:
            headers["Content-Type"] = content_type
        return headers

    def _edge(self, function: str, payload: dict, timeout: int = 40) -> dict:
        import requests

        self._ensure_session()
        try:
            response = requests.post(f"{self.url}/functions/v1/{function}", json=payload,
                                     headers=self._headers(), timeout=timeout)
        except requests.RequestException as exc:
            raise self._translate(exc) from None
        try:
            body = response.json()
        except ValueError:
            body = {}
        if response.status_code == 401:
            raise SessionExpired("Your session expired. Please sign in again.")
        if response.status_code == 404:
            raise AppError(f"This feature is not set up yet (function '{function}' is not deployed). See the README.")
        if response.status_code >= 400 or (isinstance(body, dict) and body.get("error")):
            message = body.get("error") if isinstance(body, dict) else None
            raise AppError(str(message or "The request could not be completed.")[:300])
        return body if isinstance(body, dict) else {}

    def _upload(self, bucket: str, path: str, data: bytes, mime: str) -> None:
        import requests

        self._ensure_session()
        headers = self._headers(mime)
        headers["x-upsert"] = "false"
        try:
            response = requests.post(f"{self.url}/storage/v1/object/{bucket}/{quote(path)}",
                                     data=data, headers=headers, timeout=60)
        except requests.RequestException as exc:
            raise self._translate(exc) from None
        if response.status_code >= 400:
            logger.warning("Storage upload failed: %s %s", response.status_code, response.text[:200])
            raise AppError("The upload failed. Please try again.")

    def _signed_url(self, bucket: str, path: str, seconds: int = 300) -> str:
        import requests

        self._ensure_session()
        try:
            response = requests.post(f"{self.url}/storage/v1/object/sign/{bucket}/{quote(path)}",
                                     json={"expiresIn": seconds}, headers=self._headers(), timeout=30)
        except requests.RequestException as exc:
            raise self._translate(exc) from None
        if response.status_code >= 400:
            raise AppError("Could not open the file.")
        signed = response.json().get("signedURL") or response.json().get("signedUrl")
        if not signed:
            raise AppError("Could not open the file.")
        return f"{self.url}/storage/v1{signed}" if signed.startswith("/") else signed

    def _public_prefix(self) -> str:
        return f"{self.url}/storage/v1/object/public/{ASSETS_BUCKET}/{self._rid()}/"

    def _own_asset_url(self, value: Any, label: str) -> str | None:
        """Only accept image links that point into THIS restaurant's storage folder."""
        if not value:
            return None
        value = str(value)
        if not value.startswith(self._public_prefix()):
            raise AppError(f"{label} is not valid.")
        return value

    # =======================================================================
    # AUTHENTICATION
    # =======================================================================
    def sign_in(self, email: str, password: str) -> dict:
        email = clean_email(email)
        if not password:
            raise AppError("Please enter your password.")
        self._hard_reset()
        try:
            result = self.client.auth.sign_in_with_password({"email": email, "password": password})
        except Exception as exc:
            raise self._translate(exc) from None
        if not result or not result.session:
            raise AppError("Sign-in failed. Please try again.")
        self._store_session(result.session)
        try:
            data = self._call("get_my_context") or {}
            if not data.get("role") or data.get("is_active") is False:
                raise AppError("Your account is not active. Please contact your administrator.")
        except Exception:
            self._hard_reset()
            raise
        self._ctx = {
            "user_id": data.get("user_id"), "email": data.get("email") or email,
            "full_name": data.get("full_name"), "role": data.get("role"),
            "restaurant_id": data.get("restaurant_id"), "restaurant_name": data.get("restaurant_name"),
            "employee_id": data.get("employee_id"),
        }
        return self.ctx

    def sign_out(self) -> None:
        try:
            self.client.auth.sign_out()
        except Exception:
            pass
        self._hard_reset()

    def send_password_reset(self, email: str) -> None:
        email = clean_email(email)
        redirect = _secret("APP_URL")
        try:
            if redirect:
                self.client.auth.reset_password_for_email(email, {"redirect_to": redirect})
            else:
                self.client.auth.reset_password_for_email(email)
        except Exception as exc:
            raise self._translate(exc) from None

    def verify_recovery(self, token_hash: str) -> None:
        if not _HASH_RE.match(token_hash or ""):
            raise AppError("This link is invalid or has expired.")
        try:
            result = self.client.auth.verify_otp({"token_hash": token_hash, "type": "recovery"})
        except Exception as exc:
            raise self._translate(exc) from None
        if not result or not result.session:
            raise AppError("This link is invalid or has expired.")
        self._store_session(result.session)

    def update_password(self, new_password: str) -> None:
        check_password(new_password)
        self._ensure_session()
        try:
            self.client.auth.update_user({"password": new_password})
        except Exception as exc:
            raise self._translate(exc) from None

    # =======================================================================
    # PUBLIC SETTINGS, LABELS, ACCESS GATE, MESSAGES
    # =======================================================================
    def get_public_settings(self) -> dict:
        data = self._cached("public_settings", 60, lambda: self._call("get_public_settings"))
        result = _with_defaults(data, PUBLIC_SETTING_DEFAULTS)
        return {k: (v if isinstance(v, str) else "") for k, v in result.items()}

    def get_labels(self) -> dict:
        data = self._cached("labels", 60, lambda: self._call("get_labels")) or {}
        return {str(k): str(v) for k, v in data.items() if isinstance(v, str) and v.strip()}

    def get_access_gate(self) -> dict:
        if self._ctx.get("role") == "super_admin":
            return {"allowed": True, "status": "active", "plan_name": "Platform",
                    "expiry_date": None, "days_left": None, "reason": None}
        gate = self._cached("gate", 30, lambda: self._call("get_access_gate")) or {}
        allowed = bool(gate.get("allowed"))  # fail closed
        return {"allowed": allowed, "status": gate.get("status"), "plan_name": gate.get("plan_name"),
                "expiry_date": gate.get("expiry_date"), "days_left": gate.get("days_left"),
                "reason": gate.get("reason") if not allowed else None}

    def list_messages(self) -> list[dict]:
        res = self._exec(self._table("restaurant_messages").select("*").eq("restaurant_id", self._rid())
                         .order("created_at", desc=True).limit(100))
        return res.data or []

    def mark_message_read(self, message_id: str) -> None:
        self._call("mark_message_read", {"p_message_id": clean_uuid(message_id, "Message")})

    # =======================================================================
    # SUPER ADMIN
    # =======================================================================
    def admin_overview(self, date_from: Any, date_to: Any) -> dict:
        self._need("super_admin")
        lo, hi = as_iso_date(date_from), as_iso_date(date_to)
        data = self._call("admin_overview", {"p_from": lo, "p_to": hi})
        return _with_defaults(data, {
            "total_restaurants": 0, "active_restaurants": 0, "trial_accounts": 0,
            "expired_subscriptions": 0, "suspended_restaurants": 0, "pending_payments": 0,
            "total_orders": 0, "total_sales": 0, "daily_sales": [], "top_restaurants": [],
        })

    def admin_list_restaurants(self, search: str | None = None) -> list[dict]:
        self._need("super_admin")
        term = _like(clean_text(search, "Search", 80)) or None
        return self._call("admin_list_restaurants", {"p_search": term}) or []

    def admin_create_restaurant(self, name, phone, city, owner_name, owner_email, owner_password, plan_code) -> dict:
        self._need("super_admin")
        return self._edge("admin-users", {
            "action": "create_restaurant",
            "name": clean_text(name, "Restaurant name", 100, True),
            "phone": clean_text(phone, "Phone", 30),
            "city": clean_text(city, "City", 60),
            "owner_name": clean_text(owner_name, "Owner name", 100, True),
            "owner_email": clean_email(owner_email),
            "owner_password": check_password(owner_password),
            "plan_code": clean_code(plan_code, "Plan"),
        })

    def admin_update_restaurant(self, restaurant_id, name, phone, city) -> None:
        self._need("super_admin")
        self._call("admin_update_restaurant", {
            "p_id": clean_uuid(restaurant_id, "Restaurant"),
            "p_name": clean_text(name, "Restaurant name", 100, True),
            "p_phone": clean_text(phone, "Phone", 30), "p_city": clean_text(city, "City", 60),
        })

    def admin_set_restaurant_status(self, restaurant_id, status) -> None:
        self._need("super_admin")
        if status not in RESTAURANT_STATUSES:
            raise AppError("Invalid status.")
        self._call("admin_set_restaurant_status", {"p_id": clean_uuid(restaurant_id, "Restaurant"), "p_status": status})

    def admin_assign_owner(self, restaurant_id, email, full_name, password) -> dict:
        self._need("super_admin")
        return self._edge("admin-users", {
            "action": "assign_owner",
            "restaurant_id": clean_uuid(restaurant_id, "Restaurant"),
            "email": clean_email(email),
            "full_name": clean_text(full_name, "Owner name", 100),
            "password": check_password(password) if password else None,
        })

    def admin_list_plans(self) -> list[dict]:
        self._need("super_admin")
        res = self._exec(self._table("subscription_plans").select("*").order("sort_order"))
        return res.data or []

    def admin_save_plan(self, code, name, price_pkr, duration_days, is_active, description) -> None:
        self._need("super_admin")
        self._call("admin_save_plan", {
            "p_code": clean_code(code, "Plan"),
            "p_name": clean_text(name, "Plan name", 60, True),
            "p_price": clean_amount(price_pkr, "Price", 0, 10_000_000),
            "p_duration_days": clean_int(duration_days, "Duration", 1, 3660),
            "p_is_active": bool(is_active),
            "p_description": clean_text(description, "Description", 500),
        })
        self._cache.clear()

    def admin_get_subscription(self, restaurant_id) -> dict | None:
        self._need("super_admin")
        res = self._exec(self._table("subscriptions").select("*")
                         .eq("restaurant_id", clean_uuid(restaurant_id, "Restaurant")).limit(1))
        return (res.data or [None])[0]

    def admin_set_subscription(self, restaurant_id, plan_code, status, start_date, expiry_date) -> None:
        self._need("super_admin")
        if status not in SUBSCRIPTION_STATUSES:
            raise AppError("Invalid subscription status.")
        start, expiry = as_iso_date(start_date, "Start date"), as_iso_date(expiry_date, "Expiry date")
        if expiry < start:
            raise AppError("The expiry date must be on or after the start date.")
        self._call("admin_set_subscription", {
            "p_restaurant_id": clean_uuid(restaurant_id, "Restaurant"),
            "p_plan_code": clean_code(plan_code, "Plan"), "p_status": status,
            "p_start": start, "p_expiry": expiry,
        })

    def admin_renew_subscription(self, restaurant_id, plan_code, days) -> None:
        self._need("super_admin")
        self._call("admin_renew_subscription", {
            "p_restaurant_id": clean_uuid(restaurant_id, "Restaurant"),
            "p_plan_code": clean_code(plan_code, "Plan"),
            "p_days": clean_int(days, "Days", 1, 3660),
        })

    def admin_extend_subscription(self, restaurant_id, days) -> None:
        self._need("super_admin")
        self._call("admin_extend_subscription", {
            "p_restaurant_id": clean_uuid(restaurant_id, "Restaurant"),
            "p_days": clean_int(days, "Days", 1, 3660),
        })

    def admin_send_expiry_reminders(self, within_days) -> int:
        self._need("super_admin")
        return int(self._call("admin_send_expiry_reminders",
                              {"p_within_days": clean_int(within_days, "Days", 1, 60)}) or 0)

    def admin_list_payments(self, status: str | None = None) -> list[dict]:
        self._need("super_admin")
        query = (self._table("subscription_payments").select("*, restaurants(name), subscription_plans(name)")
                 .order("created_at", desc=True).limit(300))
        if status:
            if status not in PAYMENT_STATUSES:
                raise AppError("Invalid payment status.")
            query = query.eq("status", status)
        rows = self._exec(query).data or []
        for row in rows:
            row["restaurant_name"] = _embedded_name(row, "restaurants")
            row["plan_name"] = _embedded_name(row, "subscription_plans")
        return rows

    def get_payment_proof_url(self, payment_id) -> str:
        res = self._exec(self._table("subscription_payments").select("proof_path")
                         .eq("id", clean_uuid(payment_id, "Payment")).limit(1))
        rows = res.data or []
        if not rows or not rows[0].get("proof_path"):
            raise AppError("No receipt was uploaded for this payment.")
        return self._signed_url(PROOFS_BUCKET, rows[0]["proof_path"], 300)

    def admin_review_payment(self, payment_id, approve: bool, note: str) -> None:
        self._need("super_admin")
        note = clean_text(note, "Note", 500)
        if not approve and not note:
            raise AppError("Please enter a rejection reason.")
        self._call("admin_review_payment", {
            "p_payment_id": clean_uuid(payment_id, "Payment"), "p_approve": bool(approve), "p_note": note,
        })

    def admin_send_message(self, restaurant_id, title, body) -> None:
        self._need("super_admin")
        self._call("admin_send_message", {
            "p_restaurant_id": clean_uuid(restaurant_id, "Restaurant") if restaurant_id else None,
            "p_title": clean_text(title, "Title", 120, True),
            "p_body": clean_text(body, "Message", 2000, True),
        })

    def admin_list_messages(self) -> list[dict]:
        self._need("super_admin")
        res = self._exec(self._table("restaurant_messages").select("*, restaurants(name)")
                         .order("created_at", desc=True).limit(200))
        rows = res.data or []
        for row in rows:
            row["restaurant_name"] = _embedded_name(row, "restaurants")
        return rows

    def admin_get_settings(self) -> dict:
        self._need("super_admin")
        rows = self._exec(self._table("system_settings").select("key, value")).data or []
        return {r["key"]: r["value"] for r in rows}

    def admin_save_setting(self, key: str, value: Any) -> None:
        self._need("super_admin")
        if key not in SETTING_KEYS:
            raise AppError("Unknown setting.")
        if key == "expiry_reminder_days":
            value = clean_int(value, "Reminder days", 1, 60)
        elif key == "ai_enabled":
            value = bool(value)
        elif key == "ui_labels":
            if not isinstance(value, dict):
                raise AppError("Labels are not valid.")
            cleaned = {}
            for k, v in value.items():
                if not _CODE_RE.match(str(k)):
                    raise AppError("Labels are not valid.")
                cleaned[str(k)] = clean_text(v, "Label", 60)
            value = {k: v for k, v in cleaned.items() if v}
        elif key == "ai_provider":
            if value not in AI_PROVIDERS:
                raise AppError("Unknown AI provider.")
        elif key == "ai_model":
            if not re.match(r"^[A-Za-z0-9._-]{1,80}$", str(value or "")):
                raise AppError("The model name may only contain letters, numbers, dots, dashes and underscores.")
        else:
            value = clean_text(value, "Text", 500)
        self._call("admin_save_setting", {"p_key": key, "p_value": value})
        self._cache.clear()

    def admin_list_audit_logs(self, search, date_from, date_to, limit=200) -> list[dict]:
        self._need("super_admin")
        lo, hi = day_bounds(date_from, date_to)
        query = (self._table("audit_logs").select("*").gte("created_at", lo).lt("created_at", hi)
                 .order("created_at", desc=True).limit(clean_int(limit, "Limit", 1, 1000)))
        term = _like(clean_text(search, "Search", 80))
        if term:
            query = query.or_(f"action.ilike.*{term}*,actor_email.ilike.*{term}*,entity.ilike.*{term}*")
        return self._exec(query).data or []

    # -- AI (key lives only in Supabase Edge Function secrets) --------------
    def ai_status(self) -> dict:
        self._need("super_admin")
        settings = self.admin_get_settings()
        status = {
            "configured": False, "masked_key": "", "providers": list(AI_PROVIDERS),
            "provider": settings.get("ai_provider") or DEFAULT_AI_PROVIDER,
            "model": settings.get("ai_model") or DEFAULT_AI_MODEL,
            "enabled": bool(settings.get("ai_enabled", False)),
        }
        try:
            info = self._edge("ai-assistant", {"action": "status"}, timeout=20)
        except SessionExpired:
            raise
        except AppError:
            return status  # function not deployed yet: the app still works without AI
        status["configured"] = bool(info.get("configured"))
        status["masked_key"] = str(info.get("masked_key") or "")
        return status

    def admin_save_ai_settings(self, provider, model, enabled) -> None:
        self._need("super_admin")
        self.admin_save_setting("ai_provider", provider)
        self.admin_save_setting("ai_model", model or DEFAULT_AI_MODEL)
        self.admin_save_setting("ai_enabled", bool(enabled))

    def ai_test_connection(self) -> tuple[bool, str]:
        self._need("super_admin")
        try:
            body = self._edge("ai-assistant", {"action": "test"}, timeout=40)
        except SessionExpired:
            raise
        except AppError as exc:
            return False, str(exc)
        return bool(body.get("ok")), str(body.get("message") or ("Connection successful." if body.get("ok") else "Connection failed."))

    # =======================================================================
    # RESTAURANT PROFILE
    # =======================================================================
    def get_restaurant(self) -> dict:
        res = self._exec(self._table("restaurants").select("*").eq("id", self._rid()).limit(1))
        if not res.data:
            raise AppError("Restaurant not found.")
        return res.data[0]

    def update_restaurant(self, fields: dict) -> None:
        self._need("owner")
        row: dict[str, Any] = {}
        if "name" in fields:
            row["name"] = clean_text(fields["name"], "Restaurant name", 100, True)
        for key, label, size in (("phone", "Phone", 30), ("address", "Address", 200), ("city", "City", 60)):
            if key in fields:
                row[key] = clean_text(fields[key], label, size) or None
        if "email" in fields:
            row["email"] = clean_email(fields["email"]) if fields["email"] else None
        if "commission_basis" in fields:
            if fields["commission_basis"] not in COMMISSION_BASES:
                raise AppError("Invalid commission basis.")
            row["commission_basis"] = fields["commission_basis"]
        if "escalation_minutes" in fields:
            row["escalation_minutes"] = clean_int(fields["escalation_minutes"], "Escalation time", 1, 120)
        for key in ("waiter_can_view_sales", "waiter_can_view_commission"):
            if key in fields:
                row[key] = bool(fields[key])
        if "logo_url" in fields:
            row["logo_url"] = self._own_asset_url(fields["logo_url"], "Logo")
        if not row:
            return
        res = self._exec(self._table("restaurants").update(row).eq("id", self._rid()))
        if not res.data:
            raise AppError("Could not save the profile.")
        if "name" in row:
            self._ctx["restaurant_name"] = row["name"]

    def _upload_asset(self, data: bytes, max_mb: int, prefix: str) -> str:
        _, ext, mime = validate_upload(data, {"png", "jpg", "webp"}, max_mb)
        path = f"{self._rid()}/{prefix}-{uuid.uuid4().hex}.{ext}"
        self._upload(ASSETS_BUCKET, path, data, mime)
        return f"{self.url}/storage/v1/object/public/{ASSETS_BUCKET}/{quote(path)}"

    def upload_logo(self, data: bytes, filename: str, content_type: str) -> str:
        self._need("owner")
        return self._upload_asset(data, 2, "logo")

    def upload_menu_image(self, data: bytes, filename: str, content_type: str) -> str:
        self._need("owner")
        return self._upload_asset(data, 3, "item")

    # =======================================================================
    # MENU
    # =======================================================================
    def _save_row(self, table: str, row_id: Any, row: dict, label: str) -> dict:
        rid = self._rid()
        if row_id:
            res = self._exec(self._table(table).update(row).eq("id", clean_uuid(row_id, label)).eq("restaurant_id", rid))
            if not res.data:
                raise AppError(f"{label} not found.")
            return res.data[0]
        res = self._exec(self._table(table).insert({**row, "restaurant_id": rid}))
        return (res.data or [{}])[0]

    def list_categories(self) -> list[dict]:
        res = self._exec(self._table("menu_categories").select("*").eq("restaurant_id", self._rid())
                         .order("sort_order").order("name"))
        return res.data or []

    def save_category(self, category_id, name, sort_order, is_active) -> dict:
        self._need("owner")
        return self._save_row("menu_categories", category_id, {
            "name": clean_text(name, "Category name", 60, True),
            "sort_order": clean_int(sort_order, "Order", 0, 10_000), "is_active": bool(is_active),
        }, "Category")

    def list_menu_items(self) -> list[dict]:
        res = self._exec(self._table("menu_items").select("*").eq("restaurant_id", self._rid()).order("name"))
        return res.data or []

    def save_menu_item(self, payload: dict) -> dict:
        self._need("owner")
        cost = payload.get("cost_price")
        row = {
            "name": clean_text(payload.get("name"), "Product name", 100, True),
            "category_id": clean_uuid(payload.get("category_id"), "Category"),
            "price": clean_amount(payload.get("price"), "Price", 0, 1_000_000, allow_zero=False),
            "cost_price": clean_amount(cost, "Cost price", 0, 1_000_000) if cost else None,
            "description": clean_text(payload.get("description"), "Description", 500) or None,
            "is_available": bool(payload.get("is_available", True)),
            "is_featured": bool(payload.get("is_featured")),
            "is_popular": bool(payload.get("is_popular")),
            "image_url": self._own_asset_url(payload.get("image_url"), "Image"),
        }
        return self._save_row("menu_items", payload.get("id"), row, "Product")

    def set_menu_item_active(self, item_id, active: bool) -> None:
        self._need("owner")
        self._save_row("menu_items", clean_uuid(item_id, "Product"), {"is_active": bool(active)}, "Product")

    # =======================================================================
    # TABLES AND QR CODES
    # =======================================================================
    def list_tables(self) -> list[dict]:
        res = self._exec(self._table("restaurant_tables").select("*").eq("restaurant_id", self._rid()).order("label"))
        return res.data or []

    def create_table(self, label, seats) -> dict:
        self._need("owner")
        return self._save_row("restaurant_tables", None, {
            "label": clean_text(label, "Table name", 30, True),
            "seats": clean_int(seats, "Seats", 1, 50),
        }, "Table")

    def set_table_active(self, table_id, active: bool) -> None:
        self._need("owner")
        self._save_row("restaurant_tables", clean_uuid(table_id, "Table"), {"is_active": bool(active)}, "Table")

    def regenerate_table_token(self, table_id) -> None:
        self._need("owner")
        self._call("regenerate_table_token", {"p_table_id": clean_uuid(table_id, "Table")})

    def customer_base_url(self) -> str:
        url = _secret("CUSTOMER_APP_URL").rstrip("/")
        return url if url and _valid_url(url) else ""

    def build_table_url(self, token: str) -> str:
        base = self.customer_base_url()
        if not base:
            raise AppError("CUSTOMER_APP_URL is not configured.")
        if not _TOKEN_RE.match(token or ""):
            raise AppError("This table has an invalid token. Regenerate its QR code.")
        return f"{base}/?t={quote(token)}"

    def make_qr_png(self, url: str) -> bytes:
        return make_qr_png(url)

    # =======================================================================
    # ORDERS
    # =======================================================================
    @staticmethod
    def _shape_order(row: dict) -> dict:
        row["items"] = row.pop("order_items", None) or []
        return row

    def list_orders(self, statuses, date_from, date_to, search, mine_only, limit) -> list[dict]:
        query = (self._table("orders")
                 .select("*, order_items(name, quantity, unit_price, line_total, note)")
                 .eq("restaurant_id", self._rid()))
        if statuses:
            bad = [s for s in statuses if s not in ORDER_STATUSES]
            if bad:
                raise AppError("Invalid status filter.")
            query = query.in_("status", list(statuses))
        if date_from and date_to:
            lo, hi = day_bounds(date_from, date_to)
            query = query.gte("created_at", lo).lt("created_at", hi)
        if mine_only and self._ctx.get("employee_id"):
            query = query.eq("waiter_id", self._ctx["employee_id"])
        term = _like(clean_text(search, "Search", 60))
        if term:
            query = query.eq("order_number", int(term)) if term.isdigit() and len(term) < 10 \
                else query.ilike("table_label", f"%{term}%")
        query = query.order("created_at", desc=bool(date_from)).limit(clean_int(limit, "Limit", 1, 1000))
        return [self._shape_order(r) for r in (self._exec(query).data or [])]

    def update_order_status(self, order_id, new_status) -> None:
        if new_status not in ORDER_STATUSES or new_status == "cancelled":
            raise AppError("Invalid status. Use the cancel action to cancel an order.")
        self._call("update_order_status", {"p_order_id": clean_uuid(order_id, "Order"), "p_new_status": new_status})

    def cancel_order(self, order_id, reason) -> None:
        self._need("owner")
        self._call("cancel_order", {"p_order_id": clean_uuid(order_id, "Order"),
                                    "p_reason": clean_text(reason, "Reason", 300) or "No reason given"})

    def refund_order(self, order_id, reason) -> None:
        self._need("owner")
        self._call("refund_order", {"p_order_id": clean_uuid(order_id, "Order"),
                                    "p_reason": clean_text(reason, "Reason", 300, True)})

    # =======================================================================
    # CUSTOMER REQUESTS
    # =======================================================================
    def list_requests(self, statuses, date_from, date_to) -> list[dict]:
        try:  # lazily flag overdue requests (no scheduler needed)
            self._call("escalate_stale_requests")
        except SessionExpired:
            raise
        except AppError:
            pass
        query = self._table("waiter_requests").select("*").eq("restaurant_id", self._rid())
        if statuses:
            if any(s not in REQUEST_STATUSES for s in statuses):
                raise AppError("Invalid status filter.")
            query = query.in_("status", list(statuses))
        if date_from and date_to:
            lo, hi = day_bounds(date_from, date_to)
            query = query.gte("created_at", lo).lt("created_at", hi)
        query = query.order("created_at", desc=bool(date_from)).limit(500)
        return self._exec(query).data or []

    def update_request_status(self, request_id, new_status) -> None:
        if new_status not in REQUEST_STATUSES or new_status == "pending":
            raise AppError("Invalid status.")
        self._call("update_request_status", {"p_request_id": clean_uuid(request_id, "Request"),
                                             "p_new_status": new_status})

    # =======================================================================
    # EMPLOYEES AND COMMISSION
    # =======================================================================
    def list_employees(self) -> list[dict]:
        self._need("owner")
        res = self._exec(self._table("restaurant_employees").select("*").eq("restaurant_id", self._rid())
                         .order("full_name"))
        return res.data or []

    def create_employee(self, email, full_name, password, phone, commission_pct) -> dict:
        self._need("owner")
        return self._edge("admin-users", {
            "action": "create_employee",
            "email": clean_email(email),
            "full_name": clean_text(full_name, "Name", 100, True),
            "password": check_password(password),
            "phone": clean_text(phone, "Phone", 30),
            "commission_pct": clean_amount(commission_pct, "Commission", 0, 100),
        })

    def update_employee(self, employee_id, full_name, phone, commission_pct, is_active) -> None:
        self._need("owner")
        self._save_row("restaurant_employees", clean_uuid(employee_id, "Employee"), {
            "full_name": clean_text(full_name, "Name", 100, True),
            "phone": clean_text(phone, "Phone", 30) or None,
            "commission_pct": clean_amount(commission_pct, "Commission", 0, 100),
            "is_active": bool(is_active),
        }, "Employee")

    def commission_report(self, date_from, date_to, waiter_id=None) -> dict:
        self._need("owner")
        data = self._call("commission_report", {
            "p_from": as_iso_date(date_from), "p_to": as_iso_date(date_to),
            "p_waiter_id": clean_uuid(waiter_id, "Waiter") if waiter_id else None,
        })
        report = _with_defaults(data, {"basis": "gross", "rows": [], "totals": {}})
        report["totals"] = _with_defaults(report["totals"], {"eligible_sales": 0, "orders": 0, "commission": 0})
        return report

    def commission_history(self, date_from, date_to, waiter_id=None) -> list[dict]:
        self._need("owner")
        return self._call("commission_history", {
            "p_from": as_iso_date(date_from), "p_to": as_iso_date(date_to),
            "p_waiter_id": clean_uuid(waiter_id, "Waiter") if waiter_id else None,
        }) or []

    # =======================================================================
    # INVENTORY
    # =======================================================================
    def list_inventory(self) -> list[dict]:
        self._need("owner")
        res = self._exec(self._table("inventory").select("*").eq("restaurant_id", self._rid()).order("name"))
        return res.data or []

    def save_inventory_item(self, payload: dict) -> None:
        self._need("owner")
        row = {
            "name": clean_text(payload.get("name"), "Item name", 80, True),
            "unit": clean_text(payload.get("unit"), "Unit", 20, True),
            "reorder_threshold": clean_amount(payload.get("reorder_threshold", 0), "Reorder threshold", 0, 10_000_000),
            "cost_per_unit": clean_amount(payload.get("cost_per_unit", 0), "Cost per unit", 0, 10_000_000),
        }
        if payload.get("id"):
            self._save_row("inventory", payload["id"], row, "Item")
            return
        opening = clean_amount(payload.get("quantity", 0), "Opening quantity", 0, 10_000_000)
        created = self._save_row("inventory", None, {**row, "quantity": 0}, "Item")
        if opening > 0 and created.get("id"):
            self._call("adjust_stock", {"p_item_id": created["id"], "p_kind": "addition",
                                        "p_quantity": opening, "p_reason": "Opening stock"})

    def adjust_stock(self, item_id, kind, quantity, reason) -> None:
        self._need("owner")
        if kind not in STOCK_KINDS:
            raise AppError("Invalid stock action.")
        self._call("adjust_stock", {
            "p_item_id": clean_uuid(item_id, "Item"), "p_kind": kind,
            "p_quantity": clean_amount(quantity, "Quantity", 0, 10_000_000, allow_zero=False),
            "p_reason": clean_text(reason, "Reason", 200),
        })

    def list_stock_movements(self, item_id=None, limit=200) -> list[dict]:
        self._need("owner")
        query = (self._table("inventory_transactions").select("*, inventory(name)")
                 .eq("restaurant_id", self._rid()).order("created_at", desc=True)
                 .limit(clean_int(limit, "Limit", 1, 1000)))
        if item_id:
            query = query.eq("inventory_id", clean_uuid(item_id, "Item"))
        rows = self._exec(query).data or []
        for row in rows:
            row["item_name"] = _embedded_name(row, "inventory")
        return rows

    # =======================================================================
    # EXPENSES AND ANALYTICS
    # =======================================================================
    def list_expenses(self, date_from, date_to) -> list[dict]:
        self._need("owner")
        res = self._exec(self._table("expenses").select("*").eq("restaurant_id", self._rid())
                         .gte("expense_date", as_iso_date(date_from)).lte("expense_date", as_iso_date(date_to))
                         .order("expense_date", desc=True).order("created_at", desc=True).limit(1000))
        return res.data or []

    def add_expense(self, expense_date, category, description, amount) -> None:
        self._need("owner")
        self._save_row("expenses", None, {
            "expense_date": as_iso_date(expense_date, "Date"),
            "category": clean_text(category, "Category", 50, True),
            "description": clean_text(description, "Description", 200) or None,
            "amount": clean_amount(amount, "Amount", 0, 100_000_000, allow_zero=False),
        }, "Expense")

    def delete_expense(self, expense_id) -> None:
        self._need("owner")
        res = self._exec(self._table("expenses").delete().eq("id", clean_uuid(expense_id, "Expense"))
                         .eq("restaurant_id", self._rid()))
        if not res.data:
            raise AppError("Expense not found.")

    def dashboard_summary(self) -> dict:
        self._need("owner")
        return _with_defaults(self._call("dashboard_summary"), {
            "today_orders": 0, "today_revenue": 0, "week_revenue": 0, "month_revenue": 0,
            "pending_orders": 0, "pending_requests": 0, "low_stock_count": 0,
            "unread_messages": 0, "recent_orders": [],
        })

    def analytics(self, date_from, date_to) -> dict:
        self._need("owner")
        data = self._call("analytics", {"p_from": as_iso_date(date_from), "p_to": as_iso_date(date_to)})
        result = _with_defaults(data, {"kpis": {}, "daily": [], "top_items": [], "waiter_sales": [],
                                       "low_stock": [], "profit_note": None})
        result["kpis"] = _with_defaults(result["kpis"], {
            "orders": 0, "completed": 0, "cancelled": 0, "revenue": 0, "avg_order_value": 0,
            "expenses": 0, "estimated_profit": None,
        })
        return result

    def waiter_stats(self, date_from, date_to) -> dict:
        self._need("waiter", "owner")
        return _with_defaults(self._call("waiter_stats", {
            "p_from": as_iso_date(date_from), "p_to": as_iso_date(date_to)}), {
            "orders": 0, "completed": 0, "cancelled": 0, "requests_handled": 0, "sales": 0,
            "commission": 0, "sales_visible": False, "commission_visible": False,
        })

    # =======================================================================
    # SUBSCRIPTION (owner side)
    # =======================================================================
    def get_my_subscription(self) -> dict:
        res = self._exec(self._table("subscriptions").select("*, subscription_plans(name)")
                         .eq("restaurant_id", self._rid()).limit(1))
        if not res.data:
            return {"plan_code": None, "plan_name": None, "status": "none", "start_date": None,
                    "expiry_date": None, "days_left": None}
        row = res.data[0]
        row["plan_name"] = _embedded_name(row, "subscription_plans")
        days_left = None
        if row.get("expiry_date"):
            days_left = (date.fromisoformat(str(row["expiry_date"])[:10]) - today_local()).days
            if days_left < 0 and row.get("status") in ("trial", "active"):
                row["status"] = "expired"
        row["days_left"] = days_left
        return row

    def list_plans(self) -> list[dict]:
        res = self._exec(self._table("subscription_plans").select("*").eq("is_active", True).order("sort_order"))
        return res.data or []

    def list_my_payments(self) -> list[dict]:
        self._need("owner")
        res = self._exec(self._table("subscription_payments").select("*, subscription_plans(name)")
                         .eq("restaurant_id", self._rid()).order("created_at", desc=True).limit(100))
        rows = res.data or []
        for row in rows:
            row["plan_name"] = _embedded_name(row, "subscription_plans")
        return rows

    def submit_payment_proof(self, plan_code, amount, reference, note, data, filename, content_type) -> None:
        """Upload a receipt to PRIVATE storage and file a *pending* payment. Never auto-approves."""
        self._need("owner")
        plan = clean_code(plan_code, "Plan")
        paid = clean_amount(amount, "Amount", 0, 100_000_000, allow_zero=False)
        ref = clean_text(reference, "Reference", 100)
        note = clean_text(note, "Note", 500)
        _, ext, mime = validate_upload(data, {"png", "jpg", "pdf"}, 5)
        path = f"{self._rid()}/{uuid.uuid4().hex}.{ext}"
        self._upload(PROOFS_BUCKET, path, data, mime)
        self._call("submit_payment_proof", {
            "p_plan_code": plan, "p_amount": paid, "p_reference": ref, "p_note": note, "p_proof_path": path,
        })
