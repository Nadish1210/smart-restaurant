// Unit tests for the customer page logic.  Run:  node --test tests/
const test = require("node:test");
const assert = require("node:assert");
const core = require("../customer/app.js");

const cats = [
  { id: "c1", name: "Burgers" }, { id: "c2", name: "Fries & Sides" },
  { id: "c3", name: "Cold Drinks" }, { id: "c4", name: "Desserts" },
];
const item = (id, category_id, name, price, extra = {}) =>
  ({ id, category_id, name, price, description: "", is_available: true, is_featured: false, is_popular: false, ...extra });
const menu = {
  categories: cats,
  items: [
    item("b1", "c1", "Classic Burger", 400),
    item("b2", "c1", "Zinger Burger", 550, { is_popular: true, description: "spicy fried chicken" }),
    item("b3", "c1", "Veggie Burger", 450, { description: "bean patty" }),
    item("s1", "c2", "Fries", 150, { is_popular: true }),
    item("s2", "c2", "Nuggets", 300),
    item("d1", "c3", "Pepsi", 100),
    item("d2", "c3", "Mint Margarita", 180, { is_featured: true }),
    item("x1", "c4", "Brownie", 250),
    item("u1", "c1", "Sold Out Burger", 999, { is_available: false }),
  ],
};
const total = (r) => r.items.reduce((s, l) => s + l.item.price * l.qty, 0);

test("formatPkr", () => {
  assert.equal(core.formatPkr(0), "Rs. 0");
  assert.equal(core.formatPkr(1250), "Rs. 1,250");
  assert.equal(core.formatPkr("1500"), "Rs. 1,500");
  assert.equal(core.formatPkr(1250.5), "Rs. 1,250.50");
  assert.equal(core.formatPkr(1234567), "Rs. 1,234,567");
  assert.equal(core.formatPkr("abc"), "Rs. 0");
  assert.equal(core.formatPkr(NaN), "Rs. 0");
});

test("token validation matches the database rule", () => {
  assert.ok(core.validToken("a".repeat(64)));
  assert.ok(core.validToken("Abc_def-1234567890"));
  for (const bad of ["", "short", "a".repeat(129), "has space aaaaaaaaaaaaaaaa", "../etc/passwd/aaaaaaaa", null, undefined, 5])
    assert.ok(!core.validToken(bad), String(bad));
});

test("category roles", () => {
  assert.equal(core.roleOf("Cold Drinks"), "drink");
  assert.equal(core.roleOf("Fries & Sides"), "side");
  assert.equal(core.roleOf("Desserts"), "dessert");
  assert.equal(core.roleOf("Burgers"), "main");
  assert.equal(core.roleOf(""), "main");
});

test("meal for 2: burger + fries + drink, never unavailable items", () => {
  const r = core.suggestMeal(menu, { people: 2 });
  assert.ok(r.ok);
  const ids = r.items.map((l) => l.item.id);
  assert.ok(!ids.includes("u1"));
  const roles = new Set(r.items.map((l) => core.roleOf(cats.find((c) => c.id === l.item.category_id).name)));
  assert.deepEqual([...roles].sort(), ["drink", "main", "side"]);
  assert.equal(r.items.filter((l) => l.item.category_id === "c1").reduce((s, l) => s + l.qty, 0), 2);
  assert.equal(r.total, total(r));
});

test("budget is respected, or an honest failure is returned", () => {
  for (const budget of [1500, 1200, 900, 800, 700]) {
    const r = core.suggestMeal(menu, { people: 2, budget });
    if (r.ok) assert.ok(r.total <= budget, `budget ${budget} got ${r.total}`);
  }
  const tight = core.suggestMeal(menu, { people: 2, budget: 1000 });
  assert.ok(tight.ok && tight.total <= 1000);
  const impossible = core.suggestMeal(menu, { people: 4, budget: 500 });
  assert.equal(impossible.ok, false);
  assert.equal(impossible.reason, "budget");
  assert.ok(impossible.minimum > 500);
});

test("preferences: spicy ranks spicy first, veg removes meat", () => {
  const spicy = core.suggestMeal(menu, { people: 1, prefer: "spicy" });
  assert.equal(spicy.items.find((l) => l.item.category_id === "c1").item.id, "b2");
  const veg = core.suggestMeal(menu, { people: 2, prefer: "veg" });
  assert.ok(veg.ok);
  assert.ok(!veg.items.some((l) => l.item.id === "b2"));
  assert.ok(veg.items.some((l) => l.item.id === "b3" || l.item.id === "b1"));
});

test("no mains available", () => {
  const r = core.suggestMeal({ categories: cats, items: [item("d1", "c3", "Pepsi", 100)] }, { people: 2 });
  assert.deepEqual([r.ok, r.reason], [false, "no_mains"]);
});

test("people is clamped", () => {
  assert.equal(core.suggestMeal(menu, { people: 999 }).items.filter((l) => l.item.category_id === "c1").reduce((s, l) => s + l.qty, 0), 20);
  assert.equal(core.suggestMeal(menu, { people: "x" }).items.filter((l) => l.item.category_id === "c1").reduce((s, l) => s + l.qty, 0), 2);
});

test("cart reconcile: price change, removed item", () => {
  const cart = [
    { id: "b1", name: "Classic Burger", qty: 2, note: "", seen_price: 350 },
    { id: "u1", name: "Sold Out Burger", qty: 1, note: "", seen_price: 999 },
    { id: "gone", name: "Deleted Item", qty: 1, note: "", seen_price: 10 },
    { id: "s1", name: "Fries", qty: 1, note: "extra salt", seen_price: 150 },
  ];
  const r = core.reconcileCart(cart, menu.items);
  assert.deepEqual(r.removed.sort(), ["Deleted Item", "Sold Out Burger"]);
  assert.deepEqual(r.changed, [{ name: "Classic Burger", from: 350, to: 400 }]);
  assert.deepEqual(r.cart.map((l) => [l.id, l.qty, l.seen_price, l.note]), [["b1", 2, 400, ""], ["s1", 1, 150, "extra salt"]]);
});

test("cart totals use current prices", () => {
  const by = Object.fromEntries(menu.items.map((i) => [i.id, i]));
  assert.equal(core.cartTotal([{ id: "b1", qty: 2 }, { id: "s1", qty: 3 }, { id: "nope", qty: 9 }], by), 1250);
  assert.equal(core.cartCount([{ qty: 2 }, { qty: 3 }]), 5);
});

test("idempotency signature changes only when the cart changes", () => {
  const a = [{ id: "b1", qty: 1, note: "" }];
  assert.equal(core.cartSignature(a, "n"), core.cartSignature([{ id: "b1", qty: 1, note: "" }], "n"));
  assert.notEqual(core.cartSignature(a, "n"), core.cartSignature([{ id: "b1", qty: 2, note: "" }], "n"));
  assert.notEqual(core.cartSignature(a, "n"), core.cartSignature(a, "other note"));
});
