// Tests for the Edge Function logic using fake Supabase/Gemini clients (no network).
// Run:  node --test tests/edge_functions.test.js     (needs Node 22.13+)
const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const { stripTypeScriptTypes } = require("node:module");

function loadLogic(file) {
  const src = fs.readFileSync(path.join(__dirname, "..", "supabase", "functions", file, "index.ts"), "utf8");
  const region = src.split("// <logic>")[1].split("// </logic>")[0].replace(/^export /gm, "");
  const js = stripTypeScriptTypes(region, { mode: "transform" });
  return new Function(`${js}\nreturn { ${[...js.matchAll(/^(?:async )?(?:function|class) (\w+)/gm)].map((m) => m[1]).join(", ")} };`)();
}
const body = async (res) => ({ status: res.status, ...(await res.json()) });
const post = (payload, token = "sa-token") =>
  new Request("https://x.test/fn", { method: "POST", headers: { Authorization: token ? `Bearer ${token}` : "", "Content-Type": "application/json" }, body: JSON.stringify(payload) });

// ------------------------------------------------------------------ admin-users
const au = loadLogic("admin-users");
const SA = "11111111-1111-4111-8111-111111111111", OWN = "22222222-2222-4222-8222-222222222222", WAI = "33333333-3333-4333-8333-333333333333";
const RID = "44444444-4444-4444-8444-444444444444";

function fakeAdmin({ rpcResults = {}, existing = {} } = {}) {
  const calls = { createUser: [], deleteUser: [], rpc: [] };
  const users = { "sa-token": SA, "owner-token": OWN, "waiter-token": WAI };
  const profiles = { [SA]: { role: "super_admin", is_active: true }, [OWN]: { role: "owner", is_active: true, restaurant_id: RID }, [WAI]: { role: "waiter", is_active: true, restaurant_id: RID } };
  return {
    calls,
    auth: {
      getUser: async (t) => (users[t] ? { data: { user: { id: users[t] } }, error: null } : { data: { user: null }, error: { message: "bad jwt" } }),
      admin: {
        createUser: async (a) => { calls.createUser.push(a); return rpcResults.createUserError ? { data: null, error: { message: rpcResults.createUserError } } : { data: { user: { id: "55555555-5555-4555-8555-555555555555" } }, error: null }; },
        deleteUser: async (id) => { calls.deleteUser.push(id); return { error: null }; },
      },
    },
    from: () => { let id; const q = { select: () => q, eq: (_c, v) => { id = v; return q; }, maybeSingle: async () => ({ data: profiles[id] ?? null, error: null }) }; return q; },
    rpc: async (name, params) => {
      calls.rpc.push([name, params]);
      if (name === "service_find_user_by_email") return { data: existing[params.p_email] ?? null, error: null };
      if (rpcResults[name]) return rpcResults[name];
      return { data: { restaurant_id: RID, employee_id: "e1", owner_id: "o1" }, error: null };
    },
  };
}
const newRestaurant = (extra = {}) => ({ action: "create_restaurant", name: "Cafe", phone: "1", city: "Lahore", owner_name: "Ali", owner_email: "Ali@Example.com", owner_password: "Passw0rdX", plan_code: "free_trial", ...extra });

test("admin-users: caller must be signed in", async () => {
  const a = fakeAdmin();
  assert.equal((await au.handle(post(newRestaurant(), ""), a)).status, 401);
  assert.equal((await au.handle(post(newRestaurant(), "forged"), a)).status, 401);
  assert.equal(a.calls.createUser.length, 0);
});

test("admin-users: wrong roles are refused BEFORE any account is created", async () => {
  for (const [token, payload] of [
    ["owner-token", newRestaurant()], ["waiter-token", newRestaurant()],
    ["waiter-token", { action: "create_employee", email: "w@x.com", full_name: "W", password: "Passw0rd1" }],
    ["sa-token", { action: "create_employee", email: "w@x.com", full_name: "W", password: "Passw0rd1" }],
    ["owner-token", { action: "assign_owner", restaurant_id: RID, email: "o@x.com", password: "Passw0rd1" }],
  ]) {
    const a = fakeAdmin();
    const r = await body(await au.handle(post(payload, token), a));
    assert.equal(r.status, 403, `${token} ${payload.action}`);
    assert.equal(a.calls.createUser.length, 0);
    assert.equal(a.calls.rpc.filter(([n]) => n.startsWith("service_create") || n === "service_assign_owner").length, 0);
  }
});

test("admin-users: create_restaurant success uses the verified caller, never the request body", async () => {
  const a = fakeAdmin();
  const r = await body(await au.handle(post(newRestaurant({ actor: WAI, p_actor: WAI })), a));
  assert.equal(r.status, 200);
  assert.equal(r.ok, true);
  assert.equal(a.calls.createUser.length, 1);
  assert.equal(a.calls.createUser[0].email, "ali@example.com");
  assert.equal(a.calls.createUser[0].email_confirm, true);
  assert.ok(!JSON.stringify(a.calls.createUser[0].user_metadata).includes("role"), "role must not be stored in user metadata");
  const [, params] = a.calls.rpc.find(([n]) => n === "service_create_restaurant");
  assert.equal(params.p_actor, SA);
  assert.equal(params.p_owner_email, "ali@example.com");
});

test("admin-users: duplicate email and weak password create nothing", async () => {
  let a = fakeAdmin({ existing: { "ali@example.com": WAI } });
  let r = await body(await au.handle(post(newRestaurant()), a));
  assert.equal(r.status, 409);
  assert.equal(a.calls.createUser.length, 0);
  for (const pw of ["short1", "allletters", "12345678", ""]) {
    a = fakeAdmin();
    r = await body(await au.handle(post(newRestaurant({ owner_password: pw })), a));
    assert.equal(r.status, 400, pw);
    assert.equal(a.calls.createUser.length, 0);
  }
  a = fakeAdmin();
  assert.equal((await au.handle(post(newRestaurant({ owner_email: "not-an-email" })), a)).status, 400);
});

test("admin-users: database failure removes the half-created login", async () => {
  const a = fakeAdmin({ rpcResults: { service_create_restaurant: { data: null, error: { code: "P0001", message: "That plan does not exist or is not active." } } } });
  const r = await body(await au.handle(post(newRestaurant()), a));
  assert.equal(r.status, 400);
  assert.equal(r.error, "That plan does not exist or is not active.");
  assert.deepEqual(a.calls.deleteUser, ["55555555-5555-4555-8555-555555555555"]);
});

test("admin-users: internal database errors do not leak details", async () => {
  const a = fakeAdmin({ rpcResults: { service_create_restaurant: { data: null, error: { code: "XX000", message: "relation secret_table column x does not exist" } } } });
  const r = await body(await au.handle(post(newRestaurant()), a));
  assert.equal(r.status, 500);
  assert.ok(!/secret_table/.test(r.error));
  const b = fakeAdmin({ rpcResults: { service_create_restaurant: { data: null, error: { code: "42501", message: "permission denied" } } } });
  assert.equal((await au.handle(post(newRestaurant()), b)).status, 403);
});

test("admin-users: assign_owner reuses an existing login and only cleans up accounts it created", async () => {
  let a = fakeAdmin({ existing: { "boss@x.com": "66666666-6666-4666-8666-666666666666" } });
  let r = await body(await au.handle(post({ action: "assign_owner", restaurant_id: RID, email: "boss@x.com", full_name: "Boss" }), a));
  assert.equal(r.status, 200);
  assert.equal(a.calls.createUser.length, 0);
  assert.equal(a.calls.rpc.find(([n]) => n === "service_assign_owner")[1].p_user_id, "66666666-6666-4666-8666-666666666666");
  a = fakeAdmin({ existing: { "boss@x.com": "66666666-6666-4666-8666-666666666666" }, rpcResults: { service_assign_owner: { data: null, error: { code: "P0001", message: "That email already belongs to another account." } } } });
  r = await body(await au.handle(post({ action: "assign_owner", restaurant_id: RID, email: "boss@x.com", full_name: "Boss" }), a));
  assert.equal(r.status, 400);
  assert.equal(a.calls.deleteUser.length, 0, "must never delete a pre-existing account");
  a = fakeAdmin();
  r = await body(await au.handle(post({ action: "assign_owner", restaurant_id: RID, email: "new@x.com", full_name: "New" }), a));
  assert.equal(r.status, 400, "a new account needs a password");
  r = await body(await au.handle(post({ action: "assign_owner", restaurant_id: "not-a-uuid", email: "new@x.com", password: "Passw0rd1" }), a));
  assert.equal(r.status, 400);
});

test("admin-users: create_employee validates input and cleans up on failure", async () => {
  const good = { action: "create_employee", email: "w@x.com", full_name: "Waiter", password: "Passw0rd1", phone: "03", commission_pct: 7.5 };
  let a = fakeAdmin();
  let r = await body(await au.handle(post(good, "owner-token"), a));
  assert.equal(r.status, 200);
  const [, p] = a.calls.rpc.find(([n]) => n === "service_create_employee");
  assert.equal(p.p_actor, OWN);
  assert.equal(p.p_pct, 7.5);
  for (const bad of [{ commission_pct: 150 }, { commission_pct: -1 }, { commission_pct: "abc" }, { full_name: "" }]) {
    a = fakeAdmin();
    assert.equal((await au.handle(post({ ...good, ...bad }, "owner-token"), a)).status, 400, JSON.stringify(bad));
    assert.equal(a.calls.createUser.length, 0);
  }
  a = fakeAdmin({ rpcResults: { service_create_employee: { data: null, error: { code: "P0001", message: "Your subscription is not active. Please renew it to continue." } } } });
  r = await body(await au.handle(post(good, "owner-token"), a));
  assert.equal(r.status, 400);
  assert.equal(a.calls.deleteUser.length, 1);
});

test("admin-users: method, action and crash handling", async () => {
  const a = fakeAdmin();
  assert.equal((await au.handle(new Request("https://x.test", { method: "OPTIONS" }), a)).status, 200);
  assert.equal((await au.handle(new Request("https://x.test", { method: "GET" }), a)).status, 405);
  assert.equal((await au.handle(post({ action: "drop_everything" }), a)).status, 400);
  const broken = { ...a, auth: { ...a.auth, getUser: async () => { throw new Error("boom with secret details"); } } };
  const r = await body(await au.handle(post(newRestaurant()), broken));
  assert.equal(r.status, 500);
  assert.ok(!/secret/.test(r.error));
});

// ------------------------------------------------------------------ ai-assistant
const ai = loadLogic("ai-assistant");
const KEY = "AIzaSyDUMMYKEY1234567890abcdefghijkl";
const TOKEN = "t".repeat(40);
const menu = {
  categories: [{ id: "c1", name: "Burgers" }, { id: "c2", name: "Drinks" }],
  items: [
    { id: "i1", category_id: "c1", name: "Zinger", description: "spicy", price: 500, is_available: true },
    { id: "i2", category_id: "c1", name: "Veggie", description: "", price: 450, is_available: true },
    { id: "i3", category_id: "c2", name: "Pepsi", description: "", price: 100, is_available: true },
    { id: "i4", category_id: "c1", name: "Sold out", description: "", price: 5, is_available: false },
  ],
};

test("ai: validateAiOutput keeps only real, available dishes and uses database prices", () => {
  const out = ai.validateAiOutput({ message: "Enjoy", items: [{ id: "i1", qty: 2, price: 1 }, { id: "i3", qty: 1 }, { id: "i4", qty: 1 }, { id: "made-up", qty: 1 }, { id: "i2", qty: 99 }, { id: "i2", qty: 1.5 }, { id: 7, qty: 1 }] }, menu, null);
  assert.deepEqual(out.items.map((l) => [l.id, l.qty, l.price, l.line_total]), [["i1", 2, 500, 1000], ["i3", 1, 100, 100]]);
  assert.equal(out.total, 1100);
});

test("ai: duplicates merge, budget is enforced, junk is rejected", () => {
  const merged = ai.validateAiOutput({ message: "m", items: [{ id: "i3", qty: 2 }, { id: "i3", qty: 3 }] }, menu, null);
  assert.equal(merged.items[0].qty, 5);
  const trimmed = ai.validateAiOutput({ message: "m", items: [{ id: "i1", qty: 1 }, { id: "i2", qty: 1 }, { id: "i3", qty: 1 }] }, menu, 1000);
  assert.ok(trimmed.total <= 1000 && trimmed.items.length === 2 && /budget/.test(trimmed.message), JSON.stringify(trimmed));
  assert.equal(ai.validateAiOutput({ message: "m", items: [{ id: "i1", qty: 1 }] }, menu, 100), null);
  for (const junk of [null, "text", 5, {}, { items: "x" }, { message: "x", items: [] }, { message: "x", items: [{ id: "nope", qty: 1 }] }])
    assert.equal(ai.validateAiOutput(junk, menu, null), null, JSON.stringify(junk));
  const long = ai.validateAiOutput({ message: "x".repeat(2000) + "\u0000", items: [{ id: "i3", qty: 1 }] }, menu, null);
  assert.ok(long.message.length <= 500);
});

test("ai: limiter and key masking", () => {
  const l = new ai.Limiter(3, 1000);
  assert.deepEqual([l.allow("a", 0), l.allow("a", 1), l.allow("a", 2), l.allow("a", 3)], [true, true, true, false]);
  assert.equal(l.allow("b", 3), true);
  assert.equal(l.allow("a", 1500), true);
  assert.equal(ai.maskKey(KEY), "AIza••••ijkl");
  assert.ok(!ai.maskKey(KEY).includes("DUMMYKEY"));
  assert.equal(ai.maskKey("short"), "••••");
});

function aiDeps({ settings = { ai_enabled: true, ai_model: "gemini-2.5-flash" }, profile = { role: "super_admin", is_active: true }, gemini, menuResult = { data: menu, error: null }, key = KEY } = {}) {
  const seen = { fetch: [] };
  return {
    seen,
    env: (n) => (n === "GEMINI_API_KEY" ? key : undefined),
    admin: {
      auth: { getUser: async (t) => (t === "sa-token" ? { data: { user: { id: "u" } }, error: null } : { data: { user: null }, error: { message: "x" } }) },
      from: (table) => { const q = { select: () => q, eq: () => q, in: () => Promise.resolve({ data: Object.entries(settings).map(([key, value]) => ({ key, value })) }), maybeSingle: async () => ({ data: profile }) }; return q; },
    },
    anon: { rpc: async () => menuResult },
    fetchFn: async (url, init) => { seen.fetch.push({ url, init }); return gemini ? gemini(url, init) : new Response(JSON.stringify({ candidates: [{ content: { parts: [{ text: "{}" }] } }] }), { status: 200 }); },
  };
}
const geminiSays = (obj, status = 200) => async () => new Response(JSON.stringify(status === 200 ? { candidates: [{ content: { parts: [{ text: JSON.stringify(obj) }] } }] } : { error: { message: "nope" } }), { status });
const rec = (extra = {}, tok = TOKEN) => post({ action: "recommend", token: tok, prompt: "spicy meal for two under Rs. 1500", budget: 1500, ...extra }, "");

test("ai: status and test are Super Admin only; the key is never returned", async () => {
  let r = await body(await ai.handle(post({ action: "status" }, ""), aiDeps()));
  assert.equal(r.status, 401);
  r = await body(await ai.handle(post({ action: "status" }, "sa-token"), aiDeps({ profile: { role: "owner", is_active: true } })));
  assert.equal(r.status, 403);
  const ok = await ai.handle(post({ action: "status" }, "sa-token"), aiDeps());
  const text = await ok.text();
  assert.ok(text.includes("AIza••••ijkl") && !text.includes(KEY) && JSON.parse(text).configured === true);
  const none = await body(await ai.handle(post({ action: "status" }, "sa-token"), aiDeps({ key: "" })));
  assert.equal(none.configured, false);
});

test("ai: connection test reports problems without leaking details", async () => {
  const good = await body(await ai.handle(post({ action: "test" }, "sa-token"), aiDeps({ gemini: geminiSays({}) })));
  assert.equal(good.ok, true);
  for (const [status, re] of [[401, /rejected the API key/], [403, /rejected the API key/], [404, /not found/], [429, /quota/], [500, /code 500/]]) {
    const r = await body(await ai.handle(post({ action: "test" }, "sa-token"), aiDeps({ gemini: geminiSays({}, status) })));
    assert.ok(r.ok === false && re.test(r.message), `${status}: ${r.message}`);
  }
  const net = await body(await ai.handle(post({ action: "test" }, "sa-token"), aiDeps({ gemini: async () => { throw new Error("socket hang up " + KEY); } })));
  assert.ok(!net.ok && !JSON.stringify(net).includes(KEY));
  const nokey = await body(await ai.handle(post({ action: "test" }, "sa-token"), aiDeps({ key: "" })));
  assert.ok(!nokey.ok && /GEMINI_API_KEY/.test(nokey.message));
});

test("ai: recommend returns database prices, ignores prices from the model, hides the key", async () => {
  const deps = aiDeps({ gemini: geminiSays({ message: "Try these!", items: [{ id: "i1", qty: 2, price: 1 }, { id: "i3", qty: 2 }] }) });
  const res = await ai.handle(rec({}, "a".repeat(40)), deps);
  const text = await res.text();
  const r = JSON.parse(text);
  assert.equal(res.status, 200);
  assert.deepEqual([r.ok, r.total], [true, 1200]);
  assert.ok(!text.includes(KEY));
  const call = deps.seen.fetch[0];
  assert.equal(call.init.headers["x-goog-api-key"], KEY);
  assert.ok(!call.url.includes(KEY), "key must travel in a header, not the URL");
  const sent = JSON.parse(call.init.body);
  assert.ok(/untrusted/i.test(sent.systemInstruction.parts[0].text));
  assert.ok(sent.contents[0].parts[0].text.includes("Zinger") && !sent.contents[0].parts[0].text.includes("Sold out"), "only available dishes are shown to the model");
  assert.ok(!JSON.stringify(sent).includes('"cost_price"'));
});

test("ai: recommend falls back safely", async () => {
  const cases = [
    ["AI switched off", aiDeps({ settings: { ai_enabled: false } }), 200],
    ["no key", aiDeps({ key: "" }), 200],
    ["gemini 500", aiDeps({ gemini: geminiSays({}, 500) }), 200],
    ["gemini not json", aiDeps({ gemini: async () => new Response(JSON.stringify({ candidates: [{ content: { parts: [{ text: "I am a pirate" }] } }] })) }), 200],
    ["gemini invents dishes", aiDeps({ gemini: geminiSays({ message: "x", items: [{ id: "ghost", qty: 1 }] }) }), 200],
    ["gemini throws", aiDeps({ gemini: async () => { throw new Error("timeout"); } }), 200],
    ["wrong or closed QR", aiDeps({ menuResult: { data: null, error: { message: "This QR code is not valid." } } }), 200],
  ];
  let n = 0;
  for (const [name, deps, status] of cases) {
    const r = await body(await ai.handle(rec({}, String(n++).padStart(40, "f")), deps));
    assert.ok(r.status === status && r.ok === false && r.fallback === true, `${name}: ${JSON.stringify(r)}`);
  }
});

test("ai: recommend validates input and rate-limits", async () => {
  assert.equal((await ai.handle(rec({}, "short"), aiDeps())).status, 400);
  assert.equal((await ai.handle(rec({ prompt: "   " }, "b".repeat(40)), aiDeps())).status, 400);
  const sent = JSON.parse(aiDeps().seen.fetch.length ? "{}" : "{}");
  const deps = aiDeps({ gemini: geminiSays({ message: "ok", items: [{ id: "i3", qty: 1 }] }) });
  const statuses = [];
  for (let i = 0; i < 8; i++) statuses.push((await ai.handle(rec({}, "c".repeat(40)), deps)).status);
  assert.deepEqual(statuses.slice(0, 6), Array(6).fill(200));
  assert.equal(statuses[6], 429);
  const long = aiDeps({ gemini: geminiSays({ message: "ok", items: [{ id: "i3", qty: 1 }] }) });
  await ai.handle(rec({ prompt: "p".repeat(5000) }, "d".repeat(40)), long);
  assert.ok(JSON.parse(long.seen.fetch[0].init.body).contents[0].parts[0].text.length < 12000, "prompt length is capped");
  assert.equal((await ai.handle(new Request("https://x.test", { method: "GET" }), aiDeps())).status, 405);
  assert.equal((await ai.handle(post({ action: "nope" }, ""), aiDeps())).status, 400);
});
