"""Basic automated tests for pure logic in backend.py.

Run from the project root:   pip install pytest && pytest -q
These tests need no Supabase connection. They do NOT test database policies
or the customer app; see the manual testing checklist in the README.
"""
import base64
import json
import os
import sys
from datetime import date

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import backend  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32
JPG = b"\xff\xd8\xff\xe0" + b"0" * 32
PDF = b"%PDF-1.7\n" + b"0" * 32
WEBP = b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"0" * 16


def fake_jwt(role):
    def enc(obj):
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")
    return f"{enc({'alg': 'HS256'})}.{enc({'role': role})}.sig"


@pytest.mark.parametrize("value,expected", [
    (0, "Rs. 0"), (None, "Rs. 0"), (1250, "Rs. 1,250"), (1250.5, "Rs. 1,250.50"),
    ("1500", "Rs. 1,500"), (1234567, "Rs. 1,234,567"), ("abc", "Rs. 0"), (float("nan"), "Rs. 0"),
])
def test_format_pkr(value, expected):
    assert backend.format_pkr(value) == expected


def test_email_validation():
    assert backend.clean_email("  Owner@Example.COM ") == "owner@example.com"
    for bad in ["", "no-at", "a@b", "a b@c.com"]:
        with pytest.raises(backend.AppError):
            backend.clean_email(bad)


def test_password_rules():
    assert backend.check_password("abcdefg1")
    for bad in ["short1", "allletters", "12345678", "a" * 200 + "1"]:
        with pytest.raises(backend.AppError):
            backend.check_password(bad)


def test_amount_validation():
    assert backend.clean_amount("10.005", "x") == 10.01
    with pytest.raises(backend.AppError):
        backend.clean_amount(-1, "x")
    with pytest.raises(backend.AppError):
        backend.clean_amount(0, "x", allow_zero=False)
    with pytest.raises(backend.AppError):
        backend.clean_amount("abc", "x")
    with pytest.raises(backend.AppError):
        backend.clean_amount("nan", "x")


def test_day_bounds_pakistan_time():
    lo, hi = backend.day_bounds(date(2026, 10, 22), date(2026, 10, 22))
    assert lo == "2026-10-21T19:00:00Z"  # midnight PKT = 19:00 UTC the day before
    assert hi == "2026-10-22T19:00:00Z"
    with pytest.raises(backend.AppError):
        backend.day_bounds(date(2026, 10, 23), date(2026, 10, 22))


def test_upload_detects_real_type_not_name():
    assert backend.validate_upload(PNG, {"png"}, 1)[0] == "png"
    assert backend.validate_upload(JPG, {"jpg"}, 1)[2] == "image/jpeg"
    assert backend.validate_upload(WEBP, {"webp"}, 1)[0] == "webp"
    assert backend.validate_upload(PDF, {"pdf", "png"}, 1)[0] == "pdf"
    with pytest.raises(backend.AppError):
        backend.validate_upload(b"<script>alert(1)</script>", {"png", "jpg"}, 1)  # not an image
    with pytest.raises(backend.AppError):
        backend.validate_upload(PDF, {"png", "jpg", "webp"}, 1)  # PDF not allowed for images
    with pytest.raises(backend.AppError):
        backend.validate_upload(PNG + b"0" * (2 * 1024 * 1024), {"png"}, 1)  # too big
    with pytest.raises(backend.AppError):
        backend.validate_upload(b"", {"png"}, 1)


def test_service_role_key_is_refused(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://abc.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", fake_jwt("anon"))
    assert backend.is_configured()
    monkeypatch.setenv("SUPABASE_ANON_KEY", fake_jwt("service_role"))
    assert not backend.is_configured()
    assert "service-role" in backend.config_help()
    monkeypatch.setenv("SUPABASE_ANON_KEY", "sb_secret_abc123")
    assert not backend.is_configured()
    monkeypatch.setenv("SUPABASE_ANON_KEY", "sb_publishable_abc123")
    assert backend.is_configured()
    monkeypatch.setenv("SUPABASE_URL", "http://insecure.example.com")
    assert not backend.is_configured()


def make_api(monkeypatch, customer_url="https://order.example.com/"):
    monkeypatch.setenv("CUSTOMER_APP_URL", customer_url)
    api = object.__new__(backend.SmartRestaurantAPI)
    api._cache, api._ctx = {}, {}
    return api


def test_table_url_generation(monkeypatch):
    api = make_api(monkeypatch)
    token = "a" * 48
    assert api.build_table_url(token) == f"https://order.example.com/?t={token}"
    for bad in ["", "short", "has space " + "a" * 20, "../" + "a" * 30]:
        with pytest.raises(backend.AppError):
            api.build_table_url(bad)
    monkeypatch.setenv("CUSTOMER_APP_URL", "")
    with pytest.raises(backend.AppError):
        api.build_table_url(token)


def test_qr_png():
    pytest.importorskip("qrcode")
    png = backend.make_qr_png("https://order.example.com/?t=" + "a" * 48)
    assert png.startswith(b"\x89PNG")
    with pytest.raises(backend.AppError):
        backend.make_qr_png("javascript:alert(1)")


def test_state_machines_are_consistent():
    assert set(backend.ORDER_TRANSITIONS) == set(backend.ORDER_STATUSES)
    assert set(backend.REQUEST_TRANSITIONS) == set(backend.REQUEST_STATUSES)
    for table, statuses in ((backend.ORDER_TRANSITIONS, backend.ORDER_STATUSES),
                            (backend.REQUEST_TRANSITIONS, backend.REQUEST_STATUSES)):
        for nexts in table.values():
            assert set(nexts) <= set(statuses)
    assert backend.ORDER_TRANSITIONS["completed"] == [] and backend.ORDER_TRANSITIONS["cancelled"] == []
    # An order can only be completed after it was served.
    assert [s for s, n in backend.ORDER_TRANSITIONS.items() if "completed" in n] == ["served"]


class FakeError(Exception):
    def __init__(self, message, code=""):
        super().__init__(message)
        self.message, self.code = message, code


def test_error_translation(monkeypatch):
    api = make_api(monkeypatch)
    api.client = None
    api.url = "https://abc.supabase.co"
    api.anon_key = "k"
    monkeypatch.setattr(api, "_new_client", lambda: None)
    assert str(api._translate(FakeError("Order is already completed", "P0001"))) == "Order is already completed"
    assert "permission" in str(api._translate(FakeError("new row violates row-level security policy", "42501")))
    assert "already exists" in str(api._translate(FakeError("duplicate key", "23505")))
    assert "Incorrect email" in str(api._translate(FakeError("Invalid login credentials")))
    assert isinstance(api._translate(FakeError("JWT expired", "PGRST303")), backend.SessionExpired)
    generic = str(api._translate(FakeError("secret internal detail: table x column y")))
    assert "secret" not in generic and "table" not in generic  # internals are never shown


def test_setting_validation_rejects_bad_input(monkeypatch):
    api = make_api(monkeypatch)
    api._ctx = {"role": "super_admin"}
    with pytest.raises(backend.AppError):
        api.admin_save_setting("not_a_setting", "x")
    with pytest.raises(backend.AppError):
        api.admin_save_setting("ui_labels", "not a dict")
    with pytest.raises(backend.AppError):
        api.admin_save_setting("ai_provider", "unknown-provider")
    with pytest.raises(backend.AppError):
        api.admin_save_setting("ai_model", "bad model; drop table")
    with pytest.raises(backend.AppError):
        api.admin_save_setting("expiry_reminder_days", 999)


def test_role_guards_block_wrong_roles(monkeypatch):
    api = make_api(monkeypatch)
    api._ctx = {"role": "waiter", "restaurant_id": "x"}
    for call in (lambda: api.refund_order("x", "y"), lambda: api.list_employees(),
                 lambda: api.admin_list_restaurants(None), lambda: api.update_restaurant({"name": "n"})):
        with pytest.raises(backend.AppError, match="permission"):
            call()
