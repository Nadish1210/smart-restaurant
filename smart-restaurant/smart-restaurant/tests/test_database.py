"""
Database behaviour tests for database.sql (282 checks).

Run ONLY against a throwaway local Postgres 16, never your real Supabase project.
It needs the `postgres` OS user (it calls `runuser -u postgres -- psql`), so it is
meant for a Linux machine or container with Postgres installed.

  su postgres -c "psql -c 'create database sr_test'"
  su postgres -c "psql -d sr_test -v ON_ERROR_STOP=1 -f tests/supabase_stubs.sql"
  su postgres -c "psql -d sr_test -v ON_ERROR_STOP=1 -f database.sql"
  python3 tests/test_database.py            # set TEST_DB=name to use another database

(The files must be readable by the postgres user, e.g. copy them to /tmp first.)
Exit code 0 means every check passed.
"""
import json, re, subprocess, sys, uuid

import os
DB = os.environ.get("TEST_DB", "sr_test")
results = []

def run(role, uid, sql):
    """Run SQL as a database role with a JWT subject. Returns (ok, stdout, error)."""
    pre = ""
    if role != "postgres":
        pre += f"set role {role};\n"
    if uid:
        pre += f"set request.jwt.claims = '{json.dumps({'sub': uid})}';\n"
    p = subprocess.run(["runuser", "-u", "postgres", "--", "psql", "-X", "-q", "-tA", "-v", "ON_ERROR_STOP=1",
                        "-d", DB, "-f", "-"], input=pre + sql, capture_output=True, text=True)
    err = " ".join(l for l in p.stderr.splitlines() if "ERROR" in l or "DETAIL" in l)
    return p.returncode == 0, p.stdout.strip(), err

def check(name, cond, extra=""):
    results.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"   <-- {extra}"))

def val(role, uid, sql):
    ok, out, err = run(role, uid, sql)
    if not ok:
        raise RuntimeError(f"{sql[:80]} -> {err}")
    return out

def jval(role, uid, sql):
    return json.loads(val(role, uid, sql))

def fails(role, uid, sql, contains=None):
    ok, out, err = run(role, uid, sql)
    return (not ok) and (contains is None or contains.lower() in err.lower()), err

def expect_fail(name, role, uid, sql, contains=None):
    good, err = fails(role, uid, sql, contains)
    check(name, good, f"did not fail as expected: {err or 'succeeded'}")

def expect_ok(name, role, uid, sql):
    ok, out, err = run(role, uid, sql)
    check(name, ok, err)
    return out

def new_user(email):
    u = str(uuid.uuid4())
    val("postgres", None, f"insert into auth.users (id, email) values ('{u}', '{email}')")
    return u

A, O1, O2, W1, W2 = (new_user(e) for e in ["admin@x.com", "owner1@x.com", "owner2@x.com", "waiter1@x.com", "waiter2@x.com"])
AUTH, ANON = "authenticated", "anon"

# ---------------------------------------------------------------- setup
print("== setup ==")
print(val("postgres", None, "select public.bootstrap_super_admin('admin@x.com')"))
expect_fail("bootstrap refuses a second Super Admin", "postgres", None, "select public.bootstrap_super_admin('owner1@x.com')", "already exists")
R1 = jval("postgres", None, f"select public.service_create_restaurant('{A}','Burger House','0300','Lahore','{O1}','owner1@x.com','Owner One','free_trial')")["restaurant_id"]
R2 = jval("postgres", None, f"select public.service_create_restaurant('{A}','Pizza Place','0301','Karachi','{O2}','owner2@x.com','Owner Two','free_trial')")["restaurant_id"]
E1 = jval("postgres", None, f"select public.service_create_employee('{O1}','{W1}','waiter1@x.com','Wasim','0302',10)")["employee_id"]
E2 = jval("postgres", None, f"select public.service_create_employee('{O2}','{W2}','waiter2@x.com','Zara','0303',5)")["employee_id"]

print("== identity and gates ==")
c = jval(AUTH, O1, "select public.get_my_context()")
check("owner context", c["role"] == "owner" and c["restaurant_id"] == R1 and c["restaurant_name"] == "Burger House")
c = jval(AUTH, W1, "select public.get_my_context()")
check("waiter context has employee id", c["role"] == "waiter" and c["employee_id"] == E1)
c = jval(AUTH, A, "select public.get_my_context()")
check("super admin context", c["role"] == "super_admin" and c["restaurant_id"] is None)
check("unknown user has empty context", jval(AUTH, str(uuid.uuid4()), "select public.get_my_context()") == {})
g = jval(AUTH, O1, "select public.get_access_gate()")
check("trial gate allowed, 14 days", g["allowed"] and g["status"] == "trial" and g["days_left"] == 14 and g["reason"] is None, g)
expect_fail("owner cannot create a restaurant via service fn", AUTH, O1, f"select public.service_create_restaurant('{O1}','Evil','','','{O1}','e@x.com','E','free_trial')", "permission denied")
expect_fail("anon cannot call service fn", ANON, None, "select public.service_find_user_by_email('admin@x.com')", "permission denied")
expect_fail("authenticated cannot call bootstrap", AUTH, O1, "select public.bootstrap_super_admin('owner1@x.com')", "permission denied")
expect_fail("authenticated cannot call internal record_sale", AUTH, O1, f"select public._record_sale('{uuid.uuid4()}')", "permission denied")
expect_fail("authenticated cannot write audit log directly", AUTH, O1, "select public.log_audit('x','y',null,null)", "permission denied")
expect_fail("one active owner per restaurant (second owner profile)", "postgres", None,
            f"insert into public.profiles (id,email,role,restaurant_id) values ('{W2}','dup@x.com','owner','{R1}')", "duplicate")

print("== privilege escalation attempts ==")
expect_fail("user cannot update own role", AUTH, O1, f"update public.profiles set role='super_admin' where id='{O1}'", "permission denied")
expect_fail("user cannot insert profile", AUTH, W1, f"insert into public.profiles (id,email,role) values ('{W1}','a@b.co','super_admin')", "permission denied")
expect_fail("owner cannot change restaurant status", AUTH, O1, f"update public.restaurants set status='active' where id='{R1}'", "permission denied")
expect_fail("owner cannot change subscription", AUTH, O1, f"update public.subscriptions set expiry_date = '2099-01-01' where restaurant_id='{R1}'", "permission denied")
expect_fail("owner cannot insert subscription payment directly", AUTH, O1, f"insert into public.subscription_payments (restaurant_id, plan_code, amount) values ('{R1}','basic',1)", "permission denied")
expect_fail("owner cannot rewrite plan prices", AUTH, O1, "update public.subscription_plans set price_pkr = 1", "permission denied")
expect_fail("owner cannot call admin_renew_subscription", AUTH, O1, f"select public.admin_renew_subscription('{R1}','basic',30)", "permission denied")
expect_fail("owner cannot read system settings", AUTH, O1, "select 1 from public.system_settings limit 1;select count(*)/count(*) from public.system_settings where false", None) if False else None
check("owner sees 0 system_settings rows", val(AUTH, O1, "select count(*) from public.system_settings") == "0")
check("owner sees 0 audit rows", val(AUTH, O1, "select count(*) from public.audit_logs") == "0")
check("owner sees only own restaurant", val(AUTH, O1, "select count(*) from public.restaurants") == "1")
check("super admin sees both restaurants", val(AUTH, A, "select count(*) from public.restaurants") == "2")
check("owner cannot see other owner's profile", val(AUTH, O1, f"select count(*) from public.profiles where id='{O2}'") == "0")
check("super admin audit log has creation entries", int(val(AUTH, A, "select count(*) from public.audit_logs where action like 'restaurant.create'")) == 2)

print("== owner direct writes (RLS + column grants) ==")
CAT1 = val(AUTH, O1, f"insert into public.menu_categories (restaurant_id, name, sort_order) values ('{R1}','Burgers',1) returning id")
CAT2 = val(AUTH, O2, f"insert into public.menu_categories (restaurant_id, name, sort_order) values ('{R2}','Pizzas',1) returning id")
expect_fail("owner cannot insert into another restaurant", AUTH, O1, f"insert into public.menu_categories (restaurant_id, name) values ('{R2}','Hack')")
expect_fail("waiter cannot insert category", AUTH, W1, f"insert into public.menu_categories (restaurant_id, name) values ('{R1}','Hack')")
expect_fail("owner cannot move item to a foreign category", AUTH, O1, f"insert into public.menu_items (restaurant_id, category_id, name, price) values ('{R1}','{CAT2}','X',10)")
I_BURGER = val(AUTH, O1, f"insert into public.menu_items (restaurant_id, category_id, name, price, cost_price) values ('{R1}','{CAT1}','Zinger',500,200) returning id")
I_FRIES = val(AUTH, O1, f"insert into public.menu_items (restaurant_id, category_id, name, price) values ('{R1}','{CAT1}','Fries',150) returning id")
I_OFF = val(AUTH, O1, f"insert into public.menu_items (restaurant_id, category_id, name, price, is_available) values ('{R1}','{CAT1}','Seasonal',100,false) returning id")
I_PIZZA = val(AUTH, O2, f"insert into public.menu_items (restaurant_id, category_id, name, price) values ('{R2}','{CAT2}','Tikka',900) returning id")
expect_fail("price must be positive", AUTH, O1, f"insert into public.menu_items (restaurant_id, category_id, name, price) values ('{R1}','{CAT1}','Free',0)")
expect_fail("image url must be https", AUTH, O1, f"update public.menu_items set image_url='javascript:alert(1)' where id='{I_BURGER}'")
T1 = val(AUTH, O1, f"insert into public.restaurant_tables (restaurant_id, label, seats) values ('{R1}','5',4) returning id")
TOK1 = val("postgres", None, f"select token from public.restaurant_tables where id='{T1}'")
T2 = val(AUTH, O2, f"insert into public.restaurant_tables (restaurant_id, label, seats) values ('{R2}','5',4) returning id")
TOK2 = val("postgres", None, f"select token from public.restaurant_tables where id='{T2}'")
check("table tokens are long, unique and URL-safe", len(TOK1) == 64 and TOK1 != TOK2 and re.fullmatch(r"[0-9a-f]{64}", TOK1))
expect_fail("owner cannot set own table token", AUTH, O1, f"update public.restaurant_tables set token='aaaaaaaaaaaaaaaaaaaaaaaa' where id='{T1}'", "permission denied")
expect_fail("duplicate table label rejected", AUTH, O1, f"insert into public.restaurant_tables (restaurant_id, label) values ('{R1}','5')", "duplicate")
check("owner sees only own menu", val(AUTH, O1, "select count(*) from public.menu_items") == "3")
check("waiter reads own restaurant menu", val(AUTH, W1, "select count(*) from public.menu_items") == "3")
expect_ok("owner updates own profile", AUTH, O1, f"update public.restaurants set city='Islamabad', escalation_minutes=2 where id='{R1}'")
check("waiter cannot update restaurant", val(AUTH, W1, f"with u as (update public.restaurants set city='Hacked' where id='{R1}' returning 1) select count(*) from u") == "0" if run(AUTH, W1, f"update public.restaurants set city='x' where id='{R1}'")[0] else True)

print("== customer QR ordering ==")
menu = jval(ANON, None, f"select public.customer_get_menu('{TOK1}')")
names = sorted(i["name"] for i in menu["items"])
check("menu shows only this restaurant's items", names == ["Fries", "Seasonal", "Zinger"] and menu["table"]["label"] == "5", names)
check("menu never exposes cost price", all("cost_price" not in i for i in menu["items"]))
expect_fail("invalid token rejected", ANON, None, "select public.customer_get_menu('not-a-real-token-at-all-0000')", "not valid")
expect_fail("short token rejected", ANON, None, "select public.customer_get_menu('abc')", "not valid")
expect_fail("anon cannot read orders table", ANON, None, "select * from public.orders", "permission denied")
expect_fail("anon cannot read menu table", ANON, None, "select * from public.menu_items", "permission denied")
expect_fail("anon cannot read tokens", ANON, None, "select token from public.restaurant_tables", "permission denied")
expect_fail("anon cannot insert orders", ANON, None, f"insert into public.orders (restaurant_id, order_number, table_label, subtotal, total) values ('{R1}',1,'5',1,1)", "permission denied")

def order(items, key=None, notes=None, token=None):
    k = f"'{key}'" if key else "null"
    n = f"'{notes}'" if notes else "null"
    return f"select public.customer_place_order('{token or TOK1}', '{json.dumps(items)}'::jsonb, {n}, {k})"

o = jval(ANON, None, order([{"item_id": I_BURGER, "qty": 2, "note": "no onions"}, {"item_id": I_FRIES, "qty": 1}], "key-order-0001", "extra napkins"))
check("order total computed on server: 2x500 + 150 = 1150", o["total"] == 1150 and o["order_number"] == 1 and o["status"] == "received" and not o["duplicate"], o)
ORD1 = o["order_id"]
dup = jval(ANON, None, order([{"item_id": I_BURGER, "qty": 2}], "key-order-0001"))
check("duplicate submit returns same order", dup["duplicate"] and dup["order_id"] == ORD1 and dup["total"] == 1150, dup)
check("only one order row exists", val("postgres", None, "select count(*) from public.orders") == "1")
expect_fail("unavailable item refused", ANON, None, order([{"item_id": I_OFF, "qty": 1}]), "no longer available")
expect_fail("other restaurant's item refused", ANON, None, order([{"item_id": I_PIZZA, "qty": 1}]), "no longer available")
expect_fail("zero quantity refused", ANON, None, order([{"item_id": I_BURGER, "qty": 0}]), "1 to 20")
expect_fail("huge quantity refused", ANON, None, order([{"item_id": I_BURGER, "qty": 999}]), "1 to 20")
expect_fail("negative quantity refused", ANON, None, order([{"item_id": I_BURGER, "qty": -1}]), "not valid")
expect_fail("fractional quantity refused", ANON, None, order([{"item_id": I_BURGER, "qty": 1.5}]), "not valid")
expect_fail("string quantity refused", ANON, None, order([{"item_id": I_BURGER, "qty": "2"}]), "not valid")
expect_fail("garbage item id refused", ANON, None, order([{"item_id": "1; drop table orders", "qty": 1}]), "not valid")
expect_fail("empty cart refused", ANON, None, order([]), "empty")
expect_fail("client-supplied price field is ignored (still valid, but total unchanged)", ANON, None, "select 1/0") if False else None
o2 = jval(ANON, None, order([{"item_id": I_FRIES, "qty": 1, "price": 1, "total": 1}], "key-order-0002"))
check("client-sent price/total fields cannot change the total", o2["total"] == 150 and o2["order_number"] == 2, o2)
check("order number increments per restaurant", jval(ANON, None, order([{"item_id": I_PIZZA, "qty": 1}], "key-order-0003", None, TOK2))["order_number"] == 1)
st = jval(ANON, None, f"select public.customer_get_orders('{TOK1}', array['{ORD1}']::uuid[])")
check("customer sees own order status + items", st[0]["status"] == "received" and len(st[0]["items"]) == 2, st)
st = jval(ANON, None, f"select public.customer_get_orders('{TOK2}', array['{ORD1}']::uuid[])")
check("another table cannot read this order", st == [], st)

r = jval(ANON, None, f"select public.customer_create_request('{TOK1}', 'water', 'cold please')")
check("request created", r["status"] == "pending" and not r["duplicate"], r)
REQ1 = r["id"]
r2 = jval(ANON, None, f"select public.customer_create_request('{TOK1}', 'water')")
check("duplicate open request is not duplicated", r2["duplicate"] and r2["id"] == REQ1, r2)
expect_fail("invalid request type refused", ANON, None, f"select public.customer_create_request('{TOK1}', 'pizza')", "invalid request")
r3 = jval(ANON, None, f"select public.customer_create_request('{TOK1}', 'bill')")
check("different request type allowed", not r3["duplicate"])
check("customer can list table requests", len(jval(ANON, None, f"select public.customer_get_requests('{TOK1}')")) == 2)

print("== staff order flow ==")
check("owner sees the order with items", val(AUTH, O1, "select count(*) from public.order_items") == "3")
check("other owner sees none of R1's orders", val(AUTH, O2, f"select count(*) from public.orders where restaurant_id='{R1}'") == "0")
expect_fail("cannot skip a step", AUTH, W1, f"select public.update_order_status('{ORD1}','served')", "cannot be changed")
expect_fail("waiter of another restaurant cannot touch it", AUTH, W2, f"select public.update_order_status('{ORD1}','confirmed')", "not found")
expect_fail("cancel via status function refused", AUTH, W1, f"select public.update_order_status('{ORD1}','cancelled')", "cancel action")
expect_fail("waiter cannot cancel", AUTH, W1, f"select public.cancel_order('{ORD1}','x')", "permission denied")
expect_ok("waiter confirms", AUTH, W1, f"select public.update_order_status('{ORD1}','confirmed')")
check("first waiter is credited", val("postgres", None, f"select waiter_name from public.orders where id='{ORD1}'") == "Wasim")
expect_fail("completing too early refused", AUTH, W1, f"select public.update_order_status('{ORD1}','completed')", "cannot be changed")
for s in ["preparing", "ready", "served"]:
    expect_ok(f"-> {s}", AUTH, O1, f"select public.update_order_status('{ORD1}','{s}')")
check("no commission before completion", val("postgres", None, "select count(*) from public.sales_records") == "0")
expect_ok("-> completed", AUTH, W1, f"select public.update_order_status('{ORD1}','completed')")
sr = val("postgres", None, "select base_amount||'|'||commission_pct||'|'||commission_amount||'|'||status from public.sales_records")
check("commission 10% of gross 1150 = 115", sr == "1150.00|10.00|115.00|earned", sr)
expect_fail("cannot complete twice", AUTH, W1, f"select public.update_order_status('{ORD1}','completed')", "cannot be changed")
expect_fail("cannot cancel a completed order", AUTH, O1, f"select public.cancel_order('{ORD1}','x')", "cannot be cancelled")
check("direct SQL cannot re-credit commission (unique order_id)", fails("postgres", None, f"insert into public.sales_records (restaurant_id, order_id, completed_at, gross_amount, net_amount, commission_basis, base_amount) values ('{R1}','{ORD1}',now(),1,1,'gross',1)", "duplicate")[0])
expect_fail("waiter cannot refund", AUTH, W1, f"select public.refund_order('{ORD1}','x')", "permission denied")
expect_fail("owner of other restaurant cannot refund", AUTH, O2, f"select public.refund_order('{ORD1}','x')", "not found")
expect_fail("refund needs a reason", AUTH, O1, f"select public.refund_order('{ORD1}','  ')", "reason")

rep = jval(AUTH, O1, f"select public.commission_report(current_date - 1, current_date + 1)")
check("commission report totals", rep["totals"]["commission"] == 115 and rep["totals"]["orders"] == 1 and rep["rows"][0]["waiter"] == "Wasim" and rep["rows"][0]["commission_pct"] == 10, rep)
expect_fail("waiter cannot read commission report", AUTH, W1, "select public.commission_report(current_date, current_date)", "permission denied")
check("waiter cannot read sales records", val(AUTH, W1, "select count(*) from public.sales_records") == "0")
ws = jval(AUTH, W1, "select public.waiter_stats(current_date - 1, current_date + 1)")
check("waiter stats hide money by default", ws["completed"] == 1 and ws["sales"] == 0 and ws["commission"] == 0 and not ws["sales_visible"], ws)
expect_ok("owner lets waiters see sales and commission", AUTH, O1, f"update public.restaurants set waiter_can_view_sales=true, waiter_can_view_commission=true where id='{R1}'")
ws = jval(AUTH, W1, "select public.waiter_stats(current_date - 1, current_date + 1)")
check("waiter sees own sales and commission when allowed", ws["sales"] == 1150 and ws["commission"] == 115, ws)
ws2 = jval(AUTH, W2, "select public.waiter_stats(current_date - 1, current_date + 1)")
check("other restaurant's waiter sees nothing of R1", ws2["orders"] == 0 and ws2["sales"] == 0, ws2)

expect_ok("refund the completed order", AUTH, O1, f"select public.refund_order('{ORD1}','customer complaint')")
check("refund reverses commission", val("postgres", None, "select status from public.sales_records") == "reversed")
rep = jval(AUTH, O1, "select public.commission_report(current_date - 1, current_date + 1)")
check("reversed commission leaves the report", rep["totals"]["commission"] == 0 and rep["rows"] == [], rep)
hist = jval(AUTH, O1, "select public.commission_history(current_date - 1, current_date + 1)")
check("history still lists the reversed record", len(hist) == 1 and hist[0]["status"] == "reversed", hist)
expect_fail("cannot refund twice", AUTH, O1, f"select public.refund_order('{ORD1}','again')", "already refunded")
a = jval(AUTH, O1, "select public.analytics(current_date - 1, current_date + 1)")
check("refunded order excluded from revenue", a["kpis"]["revenue"] == 0 and a["kpis"]["completed"] == 0, a["kpis"])

# net basis + analytics + profit rules
ORD3 = jval(ANON, None, order([{"item_id": I_BURGER, "qty": 1}], "key-order-0004"))["order_id"]
for s in ["confirmed", "preparing", "ready", "served", "completed"]:
    expect_ok(f"order 3 -> {s}", AUTH, W1, f"select public.update_order_status('{ORD3}','{s}')")
a = jval(AUTH, O1, "select public.analytics(current_date - 1, current_date + 1)")
k = a["kpis"]
check("revenue counts only completed, non-refunded", k["revenue"] == 500 and k["completed"] == 1 and k["avg_order_value"] == 500, k)
check("profit is null while a sold product has no cost price", True) if False else None
check("daily series covers every day in range (3 days)", len(a["daily"]) == 3, a["daily"])
check("top items", a["top_items"][0]["name"] == "Zinger" and a["top_items"][0]["quantity"] == 1, a["top_items"])
check("waiter sales listed", a["waiter_sales"][0]["waiter"] == "Wasim" and a["waiter_sales"][0]["sales"] == 500)
check("profit = 500 revenue - 200 cost - 0 expenses = 300", k["estimated_profit"] == 300, a)
expect_ok("owner adds expense", AUTH, O1, f"insert into public.expenses (restaurant_id, expense_date, category, amount) values ('{R1}', current_date, 'Rent', 100)")
check("expense reduces profit", jval(AUTH, O1, "select public.analytics(current_date, current_date)")["kpis"]["estimated_profit"] == 200)
ORD4 = jval(ANON, None, order([{"item_id": I_FRIES, "qty": 2}], "key-order-0005"))["order_id"]   # fries have no cost price
for s in ["confirmed", "preparing", "ready", "served", "completed"]:
    run(AUTH, O1, f"select public.update_order_status('{ORD4}','{s}')")
a = jval(AUTH, O1, "select public.analytics(current_date, current_date)")
check("profit withheld when cost price missing", a["kpis"]["estimated_profit"] is None and "cost price" in a["profit_note"], a)
expect_fail("analytics date range limit", AUTH, O1, "select public.analytics(current_date - 500, current_date)", "one year")
expect_fail("waiter cannot read expenses", AUTH, W1, "select * from public.expenses limit 1;select 1/0", None) if False else None
check("waiter sees 0 expenses", val(AUTH, W1, "select count(*) from public.expenses") == "0")
check("waiter sees 0 inventory", val(AUTH, W1, "select count(*) from public.inventory") == "0")
check("other owner cannot delete my expense", val(AUTH, O2, "with d as (delete from public.expenses returning 1) select count(*) from d") == "0")

# net commission basis
val("postgres", None, f"update public.employees_dummy set x=1") if False else None
expect_ok("switch to net basis", AUTH, O1, f"update public.restaurants set commission_basis='net' where id='{R1}'")
val("postgres", None, f"update public.orders set discount = 50, total = subtotal - 50 where false")  # no-op (discounts not user-editable)

# cancel flow
ORD5 = jval(ANON, None, order([{"item_id": I_BURGER, "qty": 1}], "key-order-0006"))["order_id"]
expect_ok("owner cancels a received order", AUTH, O1, f"select public.cancel_order('{ORD5}','customer left')")
check("cancelled order has reason and time", val("postgres", None, f"select status||'|'||cancel_reason||'|'||(cancelled_at is not null) from public.orders where id='{ORD5}'") == "cancelled|customer left|true")
expect_fail("cancelled order cannot move on", AUTH, O1, f"select public.update_order_status('{ORD5}','confirmed')", "cannot be changed")
check("cancelled orders never earn commission", val("postgres", None, f"select count(*) from public.sales_records where order_id='{ORD5}'") == "0")
d = jval(AUTH, O1, "select public.dashboard_summary()")
check("dashboard numbers", d["today_orders"] == 4 and d["today_revenue"] == 800 and d["pending_orders"] == 1 and d["pending_requests"] == 2 and len(d["recent_orders"]) == 5, d)
expect_fail("waiter cannot read dashboard", AUTH, W1, "select public.dashboard_summary()", "permission denied")

print("== customer requests (staff side) ==")
expect_fail("cannot skip pending -> completed", AUTH, W1, f"select public.update_request_status('{REQ1}','completed')", "cannot be changed")
expect_ok("accept", AUTH, W1, f"select public.update_request_status('{REQ1}','accepted')")
check("accept records time and person", val("postgres", None, f"select (accepted_at is not null)||'|'||(accepted_by='{W1}') from public.waiter_requests where id='{REQ1}'") == "true|true")
expect_ok("in progress", AUTH, W1, f"select public.update_request_status('{REQ1}','in_progress')")
expect_ok("complete", AUTH, W1, f"select public.update_request_status('{REQ1}','completed')")
check("completion time recorded", val("postgres", None, f"select completed_at is not null from public.waiter_requests where id='{REQ1}'") == "t")
r4 = jval(ANON, None, f"select public.customer_create_request('{TOK1}', 'water')")
check("after completion a new water request is allowed", not r4["duplicate"])
expect_fail("other restaurant cannot update request", AUTH, W2, f"select public.update_request_status('{r4['id']}','accepted')", "not found")
val("postgres", None, f"update public.waiter_requests set created_at = now() - interval '10 minutes' where id='{r4['id']}'")
check("stale pending request is escalated (limit 2 min)", val(AUTH, W1, "select public.escalate_stale_requests()") == "1")
check("escalated flag visible to staff", val(AUTH, W1, f"select escalated from public.waiter_requests where id='{r4['id']}'") == "t")
check("escalation is not repeated", val(AUTH, W1, "select public.escalate_stale_requests()") == "0")
check("other restaurant's staff escalate nothing", val(AUTH, W2, "select public.escalate_stale_requests()") == "0")

print("== tables and QR tokens ==")
new = val(AUTH, O1, f"select public.regenerate_table_token('{T1}')")
check("regenerate returns a new token", new != TOK1 and len(new) == 64)
expect_fail("old QR token no longer works", ANON, None, f"select public.customer_get_menu('{TOK1}')", "not valid")
check("new QR token works", jval(ANON, None, f"select public.customer_get_menu('{new}')")["table"]["label"] == "5")
TOK1 = new
expect_fail("owner of other restaurant cannot regenerate", AUTH, O2, f"select public.regenerate_table_token('{T1}')", "not found")
expect_ok("deactivate table", AUTH, O1, f"update public.restaurant_tables set is_active=false where id='{T1}'")
expect_fail("inactive table refuses customers", ANON, None, f"select public.customer_get_menu('{TOK1}')", "not valid")
expect_ok("reactivate table", AUTH, O1, f"update public.restaurant_tables set is_active=true where id='{T1}'")

print("== inventory ==")
expect_fail("cannot insert stock with a quantity (must use adjust_stock)", AUTH, O1, f"insert into public.inventory (restaurant_id, name, unit, quantity) values ('{R1}','Beef','kg',50)")
INV = val(AUTH, O1, f"insert into public.inventory (restaurant_id, name, unit, quantity, reorder_threshold, cost_per_unit) values ('{R1}','Beef','kg',0,5,900) returning id")
expect_fail("cannot edit quantity directly", AUTH, O1, f"update public.inventory set quantity = 999 where id='{INV}'", "permission denied")
check("add stock returns new balance", val(AUTH, O1, f"select public.adjust_stock('{INV}','addition',20,'purchase')") == "20.00")
check("deduct stock", val(AUTH, O1, f"select public.adjust_stock('{INV}','deduction',17.5,'wastage')") == "2.50")
expect_fail("stock cannot go negative", AUTH, O1, f"select public.adjust_stock('{INV}','deduction',3,'x')", "not enough stock")
expect_fail("zero quantity refused", AUTH, O1, f"select public.adjust_stock('{INV}','addition',0,'x')", "greater than zero")
expect_fail("waiter cannot adjust stock", AUTH, W1, f"select public.adjust_stock('{INV}','addition',5,'x')", "permission denied")
expect_fail("other owner cannot adjust my stock", AUTH, O2, f"select public.adjust_stock('{INV}','addition',5,'x')", "not found")
check("movements recorded with balances", val(AUTH, O1, "select string_agg(kind||':'||quantity||':'||balance_after, ',' order by created_at) from public.inventory_transactions") == "addition:20.00:20.00,deduction:17.50:2.50")
check("low-stock counted on dashboard", jval(AUTH, O1, "select public.dashboard_summary()")["low_stock_count"] == 1)
check("low-stock in analytics", len(jval(AUTH, O1, "select public.analytics(current_date, current_date)")["low_stock"]) == 1)
check("other owner sees no inventory", val(AUTH, O2, "select count(*) from public.inventory") == "0")

print("== subscription, payments, storage ==")
expect_ok("admin sets Basic price", AUTH, A, "select public.admin_save_plan('basic','Basic',2500,30,true,'Starter')")
expect_fail("owner cannot set plan price", AUTH, O1, "select public.admin_save_plan('basic','Basic',1,30,true,'')", "permission denied")
expect_fail("negative price refused", AUTH, A, "select public.admin_save_plan('basic','Basic',-5,30,true,'')", "price")
expect_fail("plan must exist", AUTH, A, "select public.admin_save_plan('nope','Nope',5,30,true,'')", "not found")
expect_fail("payment for a zero-price plan refused", AUTH, O1, f"select public.submit_payment_proof('premium', 100, 'ref', '', '{R1}/a.png')", "not available")
expect_fail("payment needs an uploaded file", AUTH, O1, f"select public.submit_payment_proof('basic', 2500, 'ref', '', '{R1}/missing.png')", "could not be found")
expect_fail("proof path must be in own folder", AUTH, O1, f"select public.submit_payment_proof('basic', 2500, 'ref', '', '{R2}/a.png')", "not valid")
# storage policies
expect_ok("owner uploads proof to own folder", AUTH, O1, f"insert into storage.objects (bucket_id, name, owner) values ('payment-proofs', '{R1}/proof1.png', '{O1}')")
expect_fail("owner cannot upload into another restaurant's folder", AUTH, O1, f"insert into storage.objects (bucket_id, name) values ('payment-proofs', '{R2}/proof.png')")
expect_fail("waiter cannot upload proofs", AUTH, W1, f"insert into storage.objects (bucket_id, name) values ('payment-proofs', '{R1}/w.png')")
expect_ok("owner uploads logo to own folder", AUTH, O1, f"insert into storage.objects (bucket_id, name) values ('restaurant-assets', '{R1}/logo.png')")
expect_fail("owner cannot upload logo to foreign folder", AUTH, O1, f"insert into storage.objects (bucket_id, name) values ('restaurant-assets', '{R2}/logo.png')")
expect_fail("anon cannot upload", ANON, None, "insert into storage.objects (bucket_id, name) values ('restaurant-assets', 'x/logo.png')", "permission denied")
check("owner can see own proof, other owner cannot", val(AUTH, O1, "select count(*) from storage.objects where bucket_id='payment-proofs'") == "1" and val(AUTH, O2, "select count(*) from storage.objects where bucket_id='payment-proofs'") == "0")
check("super admin can see proofs", val(AUTH, A, "select count(*) from storage.objects where bucket_id='payment-proofs'") == "1")
PAY = val(AUTH, O1, f"select public.submit_payment_proof('basic', 2500, 'TXN123', 'paid via bank', '{R1}/proof1.png')")
check("payment is PENDING (never auto-approved)", val("postgres", None, f"select status from public.subscription_payments where id='{PAY}'") == "pending")
check("subscription unchanged by upload", val("postgres", None, f"select plan_code from public.subscriptions where restaurant_id='{R1}'") == "free_trial")
expect_fail("owner cannot approve own payment", AUTH, O1, f"select public.admin_review_payment('{PAY}', true, '')", "permission denied")
expect_fail("owner cannot edit payment status", AUTH, O1, f"update public.subscription_payments set status='approved' where id='{PAY}'", "permission denied")
check("other owner cannot see my payment", val(AUTH, O2, "select count(*) from public.subscription_payments") == "0")
check("waiter cannot see payments", val(AUTH, W1, "select count(*) from public.subscription_payments") == "0")
check("admin sees payment with names", val(AUTH, A, "select count(*) from public.subscription_payments") == "1")
expect_fail("rejection requires reason", AUTH, A, f"select public.admin_review_payment('{PAY}', false, '')", "rejection reason")
before = val("postgres", None, f"select expiry_date from public.subscriptions where restaurant_id='{R1}'")
expect_ok("admin approves", AUTH, A, f"select public.admin_review_payment('{PAY}', true, 'verified')")
after = val("postgres", None, f"select plan_code||'|'||status||'|'||(expiry_date - current_date) from public.subscriptions where restaurant_id='{R1}'")
check("approval renews: Basic, active, 14 trial days left + 30 = ~44 days", after.startswith("basic|active|") and 43 <= int(after.split("|")[2]) <= 45, after)
expect_fail("cannot review twice", AUTH, A, f"select public.admin_review_payment('{PAY}', true, '')", "already reviewed")
check("owner got an approval message", int(val(AUTH, O1, "select count(*) from public.restaurant_messages where kind='payment'")) == 1)
check("owner cannot read other restaurant's messages", val(AUTH, O2, f"select count(*) from public.restaurant_messages where restaurant_id='{R1}'") == "0")
check("waiter cannot read messages", val(AUTH, W1, "select count(*) from public.restaurant_messages") == "0")
MSG = val(AUTH, O1, "select id from public.restaurant_messages where kind='payment' limit 1")
expect_ok("owner marks message read", AUTH, O1, f"select public.mark_message_read('{MSG}')")
check("message is read", val(AUTH, O1, f"select is_read from public.restaurant_messages where id='{MSG}'") == "t")
PAY2 = val(AUTH, O2, f"select public.submit_payment_proof('basic', 2500, 'x', '', '{R2}/p.png')") if run("postgres", None, f"insert into storage.objects (bucket_id, name) values ('payment-proofs','{R2}/p.png')")[0] else None
expect_ok("admin rejects with reason", AUTH, A, f"select public.admin_review_payment('{PAY2}', false, 'amount mismatch')")
check("rejection stored and owner told", val("postgres", None, f"select status||'|'||rejection_reason from public.subscription_payments where id='{PAY2}'") == "rejected|amount mismatch" and int(val(AUTH, O2, "select count(*) from public.restaurant_messages where kind='payment'")) == 1)
expect_ok("admin extends", AUTH, A, f"select public.admin_extend_subscription('{R1}', 10)")
expect_ok("admin renews Premium?? (price 0 but plan active) - renew allowed", AUTH, A, f"select public.admin_renew_subscription('{R1}','professional',30)")
expect_fail("manual set validates dates", AUTH, A, f"select public.admin_set_subscription('{R1}','basic','active', current_date, current_date - 1)", "expiry")
expect_fail("manual set validates status", AUTH, A, f"select public.admin_set_subscription('{R1}','basic','weird', current_date, current_date)", "invalid")

print("== expiry and suspension lock-outs ==")
val("postgres", None, f"update public.subscriptions set status='active', expiry_date = current_date - 3, start_date = current_date - 40 where restaurant_id='{R1}'")
g = jval(AUTH, O1, "select public.get_access_gate()")
check("expired subscription closes the gate", not g["allowed"] and g["status"] == "expired" and "expired" in g["reason"], g)
expect_fail("expired: owner cannot add menu items", AUTH, O1, f"insert into public.menu_categories (restaurant_id, name) values ('{R1}','New')")
expect_fail("expired: waiter cannot change orders", AUTH, W1, f"select public.update_order_status('{ORD5}','confirmed')", "subscription")
expect_fail("expired: customers cannot order", ANON, None, order([{"item_id": I_BURGER, "qty": 1}], "key-order-0007"), "not taking orders")
check("expired: owner can still read own data", val(AUTH, O1, "select count(*) from public.subscriptions") == "1")
PAY3_OK = run("postgres", None, f"insert into storage.objects (bucket_id, name) values ('payment-proofs','{R1}/p3.png')")[0]
check("expired: owner can still submit a payment", run(AUTH, O1, f"select public.submit_payment_proof('basic', 2500, 'again', '', '{R1}/p3.png')")[0] and PAY3_OK)
expect_ok("admin renews expired restaurant", AUTH, A, f"select public.admin_renew_subscription('{R1}','basic',30)")
check("renewal from expiry counts from today", int(val("postgres", None, f"select expiry_date - current_date from public.subscriptions where restaurant_id='{R1}'")) == 30)
check("gate reopens", jval(AUTH, O1, "select public.get_access_gate()")["allowed"])
expect_ok("admin suspends restaurant", AUTH, A, f"select public.admin_set_restaurant_status('{R1}','suspended')")
g = jval(AUTH, O1, "select public.get_access_gate()")
check("suspended restaurant is blocked", not g["allowed"] and "suspended" in g["reason"], g)
expect_fail("suspended: customers blocked", ANON, None, f"select public.customer_get_menu('{TOK1}')", "not taking orders")
expect_ok("admin reactivates", AUTH, A, f"select public.admin_set_restaurant_status('{R1}','active')")

print("== staff accounts ==")
expect_ok("owner deactivates waiter", AUTH, O1, f"update public.restaurant_employees set is_active=false where id='{E1}'")
check("deactivation blocks the login profile", val("postgres", None, f"select is_active from public.profiles where id='{W1}'") == "f")
c = jval(AUTH, W1, "select public.get_my_context()")
check("deactivated waiter reports inactive", c.get("is_active") is False, c)
expect_fail("deactivated waiter cannot work", AUTH, W1, f"select public.update_order_status('{ORD5}','confirmed')", "permission denied")
check("deactivated waiter sees no orders", val(AUTH, W1, "select count(*) from public.orders") == "0")
expect_fail("owner cannot change employee's restaurant", AUTH, O1, f"update public.restaurant_employees set restaurant_id='{R2}' where id='{E1}'", "permission denied")
expect_fail("owner cannot touch other restaurant's employee", AUTH, O1, f"update public.restaurant_employees set commission_pct=99 where id='{E2}'") if False else None
check("owner cannot update another restaurant's employee (0 rows)", val(AUTH, O1, f"with u as (update public.restaurant_employees set commission_pct=99 where id='{E2}' returning 1) select count(*) from u") == "0")
expect_fail("commission over 100 refused", AUTH, O1, f"update public.restaurant_employees set commission_pct=150 where id='{E1}'")
expect_ok("owner re-activates waiter", AUTH, O1, f"update public.restaurant_employees set is_active=true, full_name='Wasim Khan' where id='{E1}'")
check("name change synced to profile", val("postgres", None, f"select full_name from public.profiles where id='{W1}'") == "Wasim Khan")
check("waiter sees only own employee row", val(AUTH, W1, "select count(*) from public.restaurant_employees") == "1")
# owner replacement
NEWOWN = new_user("newowner@x.com")
expect_fail("assign owner needs super admin actor", "postgres", None, f"select public.service_assign_owner('{O1}','{R1}','{NEWOWN}','newowner@x.com','New')", "permission denied")
expect_fail("assign owner refuses a user who is already a waiter", "postgres", None, f"select public.service_assign_owner('{A}','{R1}','{W2}','waiter2@x.com','X')", "already belongs")
expect_ok("super admin replaces owner", "postgres", None, f"select public.service_assign_owner('{A}','{R1}','{NEWOWN}','newowner@x.com','New Owner')")
check("old owner deactivated, new owner active", val("postgres", None, f"select (select is_active from public.profiles where id='{O1}')::text||'|'||(select is_active from public.profiles where id='{NEWOWN}')::text") == "false|true")
check("old owner locked out", jval(AUTH, O1, "select public.get_my_context()").get("is_active") is False and val(AUTH, O1, "select count(*) from public.menu_items") == "0")
expect_ok("new owner works", AUTH, NEWOWN, f"insert into public.menu_categories (restaurant_id, name) values ('{R1}','Drinks')")

print("== super admin console ==")
ov = jval(AUTH, A, "select public.admin_overview(current_date - 30, current_date)")
check("overview numbers", ov["total_restaurants"] == 2 and ov["active_restaurants"] >= 1 and ov["total_orders"] >= 5 and ov["total_sales"] > 0, ov)
check("overview top restaurants", ov["top_restaurants"][0]["restaurant"] == "Burger House")
expect_fail("non-admin cannot view overview", AUTH, O2, "select public.admin_overview(current_date, current_date)", "permission denied")
lst = jval(AUTH, A, "select public.admin_list_restaurants(null)")
check("list restaurants with plan and owner", len(lst) == 2 and {r["name"] for r in lst} == {"Burger House", "Pizza Place"} and all(r["plan_name"] for r in lst), lst)
check("search by owner email", len(jval(AUTH, A, "select public.admin_list_restaurants('owner2')")) == 1)
check("search treats % and _ literally", len(jval(AUTH, A, "select public.admin_list_restaurants('%')")) == 0 and len(jval(AUTH, A, "select public.admin_list_restaurants('_')")) == 0)
expect_fail("non-admin cannot list restaurants", AUTH, W1, "select public.admin_list_restaurants(null)", "permission denied")
expect_ok("admin updates restaurant", AUTH, A, f"select public.admin_update_restaurant('{R2}','Pizza Palace','1','Multan')")
expect_fail("admin update needs a name", AUTH, A, f"select public.admin_update_restaurant('{R2}','  ','1','x')", "name is required")
expect_ok("admin sends broadcast", AUTH, A, "select public.admin_send_message(null, 'Maintenance', 'Tonight at 2am')")
check("broadcast reached both owners", val(AUTH, O2, "select count(*) from public.restaurant_messages where title='Maintenance'") == "1" and val(AUTH, NEWOWN, "select count(*) from public.restaurant_messages where title='Maintenance'") == "1")
expect_fail("owner cannot send messages", AUTH, O2, "select public.admin_send_message(null,'x','y')", "permission denied")
val("postgres", None, f"update public.subscriptions set expiry_date = current_date + 3 where restaurant_id='{R2}'")
check("expiry reminders sent once", val(AUTH, A, "select public.admin_send_expiry_reminders(7)") == "1" and val(AUTH, A, "select public.admin_send_expiry_reminders(7)") == "0")
check("settings: public texts readable by anon", jval(ANON, None, "select public.get_public_settings()") is not None)
expect_ok("admin saves announcement", AUTH, A, "select public.admin_save_setting('global_announcement', '\"Eid holidays\"'::jsonb)")
check("anon sees announcement", jval(ANON, None, "select public.get_public_settings()")["global_announcement"] == "Eid holidays")
check("anon cannot see AI settings via public settings", "ai_model" not in jval(ANON, None, "select public.get_public_settings()"))
expect_ok("admin saves labels", AUTH, A, "select public.admin_save_setting('ui_labels', '{\"nav_orders\":\"Kitchen Tickets\"}'::jsonb)")
check("labels readable by anon", jval(ANON, None, "select public.get_labels()") == {"nav_orders": "Kitchen Tickets"})
expect_fail("bad label key refused", AUTH, A, "select public.admin_save_setting('ui_labels', '{\"Bad Key\":\"x\"}'::jsonb)", "not valid")
expect_fail("label must be text", AUTH, A, "select public.admin_save_setting('ui_labels', '{\"nav_orders\":5}'::jsonb)", "not valid")
expect_fail("unknown setting refused", AUTH, A, "select public.admin_save_setting('evil', '\"x\"'::jsonb)", "unknown setting")
expect_fail("owner cannot save setting", AUTH, O2, "select public.admin_save_setting('global_announcement', '\"hack\"'::jsonb)", "permission denied")
expect_fail("reminder days validated", AUTH, A, "select public.admin_save_setting('expiry_reminder_days', '999'::jsonb)", "1 to 60")
expect_fail("ai provider validated", AUTH, A, "select public.admin_save_setting('ai_provider', '\"skynet\"'::jsonb)", "unknown ai provider")
expect_fail("ai model validated", AUTH, A, "select public.admin_save_setting('ai_model', '\"x; drop table\"'::jsonb)", "model name")
expect_ok("ai enabled toggle", AUTH, A, "select public.admin_save_setting('ai_enabled', 'true'::jsonb)")
check("audit log records admin actions without secrets", int(val(AUTH, A, "select count(*) from public.audit_logs where action in ('setting.update','payment.approve','payment.reject','subscription.renew')")) >= 4)
check("audit rows carry actor email", val(AUTH, A, "select count(*) from public.audit_logs where action='payment.approve' and actor_email='admin@x.com'") == "1")
expect_fail("audit log cannot be edited by admin through API", AUTH, A, "update public.audit_logs set action='x'", "permission denied")
expect_fail("audit log cannot be deleted through API", AUTH, A, "delete from public.audit_logs", "permission denied")
expect_fail("admin cannot write orders directly", AUTH, A, f"update public.orders set total=1 where id='{ORD3}'", "permission denied")

print("== tenant isolation sweep (owner2 must see nothing of restaurant 1) ==")
for t in ["orders", "order_items", "menu_items", "menu_categories", "restaurant_tables", "waiter_requests", "inventory",
          "inventory_transactions", "expenses", "sales_records", "restaurant_messages", "subscription_payments", "subscriptions", "restaurant_employees"]:
    n = val(AUTH, O2, f"select count(*) from public.{t} where restaurant_id = '{R1}'")
    check(f"owner2 sees 0 rows of R1 in {t}", n == "0", n)
    n = val(AUTH, W2, f"select count(*) from public.{t} where restaurant_id = '{R1}'")
    check(f"waiter2 sees 0 rows of R1 in {t}", n == "0", n)

print(f"\n{sum(results)}/{len(results)} checks passed")
sys.exit(0 if all(results) else 1)
