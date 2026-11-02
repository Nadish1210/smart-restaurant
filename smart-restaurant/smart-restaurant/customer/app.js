/*
 * Smart Restaurant - customer ordering page.
 *
 * Opened by scanning a table's QR code:   <this page>/?t=<secret table token>
 *
 * SECURITY NOTES
 *  - This page holds only the PUBLIC anon key. It cannot read or write any
 *    table. It can only call the customer_* database functions, which look the
 *    table up by its secret token and read every price from the database.
 *  - The prices shown here are for display only. The order total is always
 *    recalculated by the database when the order is placed.
 *  - Everything that comes from the server is written with textContent
 *    (never innerHTML), so menu text cannot inject code.
 *
 * The first half of this file is plain logic (also unit-tested with Node);
 * the second half builds the screen.
 */
(function (root) {
  "use strict";

  // ===========================================================================
  // Pure logic
  // ===========================================================================
  var TOKEN_RE = /^[A-Za-z0-9_-]{16,128}$/;
  var MAX_QTY = 20;
  var MEAT_RE = /chicken|beef|mutton|lamb|fish|prawn|shrimp|meat|bbq|tikka|kebab|kabab|seekh|zinger|nihari|haleem|keema|bacon|sausage|pepperoni|steak|wings|karahi/i;
  var SPICY_RE = /spicy|hot|chilli|chili|masala|zinger|tikka|peri|fiery|jalapeno|karahi/i;
  var ROLE_RE = {
    drink: /drink|beverage|juice|shake|tea|coffee|soda|cold|lassi|water|mocktail/i,
    dessert: /dessert|sweet|ice ?cream|cake|pudding/i,
    side: /side|fries|starter|appetizer|appetiser|snack|salad|soup|extra/i
  };
  var STATUS_STEPS = ["received", "confirmed", "preparing", "ready", "served", "completed"];
  var STATUS_TEXT = {
    received: "Order received", confirmed: "Confirmed", preparing: "Being prepared", ready: "Ready",
    served: "Served", completed: "Completed", cancelled: "Cancelled"
  };
  var REQUEST_TYPES = [
    ["call_waiter", "🙋 Call service provider"], ["bill", "🧾 Request bill"], ["water", "💧 Water"],
    ["cutlery", "🍴 Cutlery"], ["assistance", "❓ Need assistance"], ["other", "✍️ Something else"]
  ];
  var REQUEST_STATUS_TEXT = {
    pending: "Waiting for a service provider", accepted: "A service provider is coming", in_progress: "On the way",
    completed: "Done", cancelled: "Cancelled"
  };

  function validToken(t) { return typeof t === "string" && TOKEN_RE.test(t); }

  function formatPkr(amount) {
    var n = Number(amount);
    if (!isFinite(n)) n = 0;
    n = Math.round(n * 100) / 100;
    var whole = Math.abs(n % 1) < 1e-9;
    var text = whole ? Math.round(n).toLocaleString("en-US") : n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    return "Rs. " + text;
  }

  function clampInt(v, lo, hi, dflt) {
    var n = parseInt(v, 10);
    if (!isFinite(n)) return dflt;
    return Math.min(hi, Math.max(lo, n));
  }

  function roleOf(categoryName) {
    var name = String(categoryName || "");
    if (ROLE_RE.drink.test(name)) return "drink";
    if (ROLE_RE.dessert.test(name)) return "dessert";
    if (ROLE_RE.side.test(name)) return "side";
    return "main";
  }

  function cartTotal(cart, itemsById) {
    var total = 0;
    cart.forEach(function (line) {
      var item = itemsById[line.id];
      if (item) total += Number(item.price) * line.qty;
    });
    return Math.round(total * 100) / 100;
  }

  function cartCount(cart) {
    return cart.reduce(function (s, l) { return s + l.qty; }, 0);
  }

  // Compares the cart with the freshly loaded menu. Removes items that are no
  // longer orderable and updates prices that changed.
  function reconcileCart(cart, items) {
    var byId = {};
    items.forEach(function (i) { byId[i.id] = i; });
    var kept = [], removed = [], changed = [];
    cart.forEach(function (line) {
      var item = byId[line.id];
      if (!item || !item.is_available) { removed.push(line.name); return; }
      var now = Number(item.price);
      if (Number(line.seen_price) !== now) {
        changed.push({ name: item.name, from: Number(line.seen_price), to: now });
      }
      kept.push({ id: line.id, name: item.name, qty: line.qty, note: line.note || "", seen_price: now });
    });
    return { cart: kept, removed: removed, changed: changed };
  }

  // A short fingerprint of the cart. The same fingerprint reuses the same
  // idempotency key, so a double tap or a lost connection can never create two orders.
  function cartSignature(cart, note) {
    return JSON.stringify([cart.map(function (l) { return [l.id, l.qty, l.note || ""]; }), note || ""]);
  }

  // "Basic" recommendations: no AI, no network. Builds a simple meal
  // (mains + sides + drinks) from the CURRENT menu and tries to fit a budget.
  function suggestMeal(menu, opts) {
    opts = opts || {};
    var people = clampInt(opts.people, 1, 20, 2);
    var budget = Number(opts.budget) > 0 ? Number(opts.budget) : null;
    var prefer = opts.prefer || "any";
    var roleByCategory = {};
    menu.categories.forEach(function (c) { roleByCategory[c.id] = roleOf(c.name); });
    var pool = menu.items.filter(function (i) { return i.is_available; });
    var text = function (i) { return (i.name || "") + " " + (i.description || ""); };
    if (prefer === "veg") pool = pool.filter(function (i) { return !MEAT_RE.test(text(i)); });

    var groups = { main: [], side: [], drink: [], dessert: [] };
    pool.forEach(function (i) { (groups[roleByCategory[i.category_id] || "main"] || groups.main).push(i); });
    var score = function (i) {
      return (i.is_popular ? 2 : 0) + (i.is_featured ? 2 : 0) + (prefer === "spicy" && SPICY_RE.test(text(i)) ? 3 : 0);
    };
    var ranked = function (a, b) { return score(b) - score(a) || Number(a.price) - Number(b.price); };
    var cheapest = function (a, b) { return Number(a.price) - Number(b.price); };
    Object.keys(groups).forEach(function (k) { groups[k].sort(ranked); });
    if (!groups.main.length) return { ok: false, reason: "no_mains" };

    function pick(list, count, mode) {
      var src = mode === "cheapest" ? list.slice().sort(cheapest) : list;
      var out = [];
      for (var k = 0; k < count && src.length; k++) out.push(src[k % src.length]);
      return out;
    }
    function build(cfg) {
      var chosen = pick(groups.main, people, cfg.mains);
      if (cfg.sides !== "none") chosen = chosen.concat(pick(groups.side, Math.ceil(people / 2), cfg.sides));
      if (cfg.drinks !== "none") chosen = chosen.concat(pick(groups.drink, people, cfg.drinks));
      var lines = {}, order = [];
      chosen.forEach(function (i) {
        if (!lines[i.id]) { lines[i.id] = { item: i, qty: 0 }; order.push(i.id); }
        lines[i.id].qty += 1;
      });
      var list = order.map(function (id) { return lines[id]; });
      var total = list.reduce(function (s, l) { return s + Number(l.item.price) * l.qty; }, 0);
      return { items: list, total: Math.round(total * 100) / 100 };
    }
    var ladder = [
      { mains: "ranked", sides: "ranked", drinks: "ranked" },
      { mains: "ranked", sides: "ranked", drinks: "cheapest" },
      { mains: "ranked", sides: "cheapest", drinks: "cheapest" },
      { mains: "cheapest", sides: "cheapest", drinks: "cheapest" },
      { mains: "cheapest", sides: "none", drinks: "cheapest" },
      { mains: "cheapest", sides: "none", drinks: "none" }
    ];
    var last = null;
    for (var s = 0; s < ladder.length; s++) {
      last = build(ladder[s]);
      if (budget === null || last.total <= budget) {
        return { ok: true, items: last.items, total: last.total, people: people, budget: budget };
      }
    }
    return { ok: false, reason: "budget", minimum: last.total };
  }

  // Upselling: if the order has a main dish but no drink / dessert, suggest up to 2 of each.
  function upsellFor(menu, cart, limit) {
    var roleByCat = {};
    menu.categories.forEach(function (c) { roleByCat[c.id] = roleOf(c.name); });
    var byId = {};
    menu.items.forEach(function (i) { byId[i.id] = i; });
    var has = { main: false, drink: false, dessert: false };
    cart.forEach(function (l) { var it = byId[l.id]; if (it) has[roleByCat[it.category_id] || "main"] = true; });
    if (!has.main && !has.side) return [];
    var out = [];
    ["drink", "dessert"].forEach(function (role) {
      if (has[role]) return;
      menu.items.filter(function (i) { return i.is_available && roleByCat[i.category_id] === role; })
        .sort(function (a, b) { return ((b.is_popular ? 2 : 0) + (b.is_featured ? 1 : 0)) - ((a.is_popular ? 2 : 0) + (a.is_featured ? 1 : 0)) || Number(a.price) - Number(b.price); })
        .slice(0, limit || 2).forEach(function (i) { out.push({ role: role, item: i }); });
    });
    return out;
  }

  function popularItems(menu, limit) {
    return menu.items.filter(function (i) { return i.is_available && (i.is_popular || i.is_featured); })
      .sort(function (a, b) { return (b.is_popular ? 1 : 0) - (a.is_popular ? 1 : 0); }).slice(0, limit || 6);
  }

  // ---- bill helpers -------------------------------------------------------------------
  function billOrders(orders) {
    return (orders || []).filter(function (o) { return o && o.status !== "cancelled" && !o.refunded; });
  }
  // Orders that still need paying: not paid, not cancelled, and not already inside a pending payment.
  function payableOrders(orders, payments) {
    var inPending = {};
    (payments || []).forEach(function (p) {
      if (p && p.status === "pending") (p.order_ids || []).forEach(function (id) { inPending[id] = true; });
    });
    return billOrders(orders).filter(function (o) { return !o.paid_at && !inPending[o.order_id]; });
  }
  function sumTotals(list) {
    var cents = 0;
    (list || []).forEach(function (o) { cents += Math.round(Number(o.total) * 100) || 0; });
    return cents / 100;
  }
  function billState(orders) {
    var live = billOrders(orders);
    if (!live.length) return "none";
    var paid = live.filter(function (o) { return o.paid_at; }).length;
    return paid === live.length ? "paid" : (paid === 0 ? "unpaid" : "partial");
  }
  var METHOD_TEXT = { online: "Online payment", cash: "Cash", card: "Card" };

  // ---- tip helpers ------------------------------------------------------------------------
  function openTips(tips) {
    return (tips || []).filter(function (t) { return t && t.status === "open" && !t.in_payment; });
  }
  function sumAmounts(list) {
    var cents = 0;
    (list || []).forEach(function (t) { cents += Math.round(Number(t.amount) * 100) || 0; });
    return cents / 100;
  }
  function tipLabel(t) {
    return t.recipient === "kitchen" ? "Kitchen" : "Service provider" + (t.waiter_name ? " (" + t.waiter_name + ")" : "");
  }

  // ---- service promise helpers -----------------------------------------------------------
  // The promised time of a cart is the slowest dish (dishes are cooked together).
  function cartPromise(cart, itemsById) {
    var m = null;
    (cart || []).forEach(function (l) {
      var it = itemsById && itemsById[l.id];
      var p = it ? Number(it.prep_minutes) : 0;
      if (p > 0 && (m === null || p > m)) m = p;
    });
    return m;
  }
  // Minutes left before an order is late (negative = late). null when there is no promise or it is over.
  function promiseLeft(o, nowMs) {
    if (!o || !(Number(o.promised_minutes) > 0)) return null;
    if (["served", "completed", "cancelled"].indexOf(o.status) >= 0) return null;
    var t = Date.parse(o.created_at);
    if (isNaN(t)) return null;
    return Number(o.promised_minutes) - (nowMs - t) / 60000;
  }

  function randomKey() {
    try {
      if (root.crypto && root.crypto.randomUUID) return root.crypto.randomUUID();
      var a = new Uint8Array(16);
      root.crypto.getRandomValues(a);
      return Array.prototype.map.call(a, function (b) { return ("0" + b.toString(16)).slice(-2); }).join("");
    } catch (e) {
      return String(Date.now()) + "-" + Math.random().toString(36).slice(2, 12);
    }
  }

  var core = {
    validToken: validToken, formatPkr: formatPkr, roleOf: roleOf, cartTotal: cartTotal, cartCount: cartCount,
    reconcileCart: reconcileCart, cartSignature: cartSignature, suggestMeal: suggestMeal, upsellFor: upsellFor,
    popularItems: popularItems, clampInt: clampInt, billOrders: billOrders, payableOrders: payableOrders,
    sumTotals: sumTotals, billState: billState, cartPromise: cartPromise, promiseLeft: promiseLeft,
    openTips: openTips, sumAmounts: sumAmounts, tipLabel: tipLabel
  };
  root.SRCore = core;
  if (typeof module !== "undefined" && module.exports) module.exports = core;

  // ===========================================================================
  // Screen
  // ===========================================================================
  if (typeof document === "undefined") return;

  var CFG = root.SMART_RESTAURANT_CONFIG || {};
  var state = {
    token: "", menu: null, itemsById: {}, category: "all", tag: "", search: "", tab: "menu",
    cart: [], orderNote: "", orderIds: [], orders: [], requests: [], placing: false,
    payments: [], paymentIds: [], payMethod: "", proof: null, paying: false,
    tips: [], tipIds: [], tipTo: "waiter", tipAmt: "", fbRating: 0, fbComment: "", fbBusy: false,
    key: null, keySig: "", cartMessage: "", aiBusy: false, suggestion: null, aiResult: null, lastMenuAt: 0
  };
  var $ = function (id) { return document.getElementById(id); };

  function h(tag, props) {
    var e = document.createElement(tag);
    Object.keys(props || {}).forEach(function (k) {
      var v = props[k];
      if (v === null || v === undefined || v === false) return;
      if (k === "class") e.className = v;
      else if (k === "text") e.textContent = v;
      else if (k.indexOf("on") === 0) e.addEventListener(k.slice(2), v);
      else e.setAttribute(k, v === true ? "" : v);
    });
    for (var i = 2; i < arguments.length; i++) {
      var kids = [].concat(arguments[i]);
      for (var j = 0; j < kids.length; j++) {
        var kid = kids[j];
        if (kid === null || kid === undefined || kid === false) continue;
        e.appendChild(kid.nodeType ? kid : document.createTextNode(String(kid)));
      }
    }
    return e;
  }
  function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }

  // ---- storage (may be blocked in private mode: never required) -------------
  function skey(name) { return "sr_" + name + "_" + state.token.slice(0, 12); }
  function load(name, dflt) {
    try { var v = root.localStorage.getItem(skey(name)); return v ? JSON.parse(v) : dflt; } catch (e) { return dflt; }
  }
  function save(name, value) {
    try { root.localStorage.setItem(skey(name), JSON.stringify(value)); } catch (e) { /* ignore */ }
  }

  // ---- network ---------------------------------------------------------------
  function ApiError(message, status) { this.message = message; this.status = status; }
  ApiError.prototype = Object.create(Error.prototype);

  function headers() {
    var hd = { "Content-Type": "application/json", apikey: CFG.SUPABASE_ANON_KEY };
    if (String(CFG.SUPABASE_ANON_KEY || "").indexOf("eyJ") === 0) hd.Authorization = "Bearer " + CFG.SUPABASE_ANON_KEY;
    return hd;
  }
  function friendly(body, status) {
    if (body && body.code === "P0001" && body.message) return String(body.message).slice(0, 300);
    if (status === 429) return "Too many requests. Please wait a moment and try again.";
    return "Something went wrong. Please try again.";
  }
  function post(url, params) {
    return root.fetch(url, { method: "POST", headers: headers(), body: JSON.stringify(params || {}), referrerPolicy: "no-referrer" })
      .then(function (res) {
        return res.json().catch(function () { return null; }).then(function (body) {
          if (!res.ok) throw new ApiError(friendly(body, res.status), res.status);
          return body;
        });
      }, function () { throw new ApiError("Cannot reach the restaurant. Check your internet connection.", 0); });
  }
  function rpc(fn, params) { return post(String(CFG.SUPABASE_URL).replace(/\/+$/, "") + "/rest/v1/rpc/" + fn, params); }

  // ---- feedback ----------------------------------------------------------------
  var toastTimer = null;
  function toast(msg) {
    var t = $("toast");
    t.textContent = msg;
    t.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { t.classList.remove("show"); }, 3200);
  }
  function fatal(title, text, retry) {
    $("app").hidden = true;
    $("fatal").hidden = false;
    $("fatalTitle").textContent = title;
    $("fatalText").textContent = text;
    var b = $("fatalRetry");
    b.hidden = !retry;
    b.onclick = retry || null;
  }

  // ---- menu ------------------------------------------------------------------------
  function setMenu(raw) {
    state.menu = {
      restaurant: raw.restaurant || {}, table: raw.table || {},
      categories: raw.categories || [], items: (raw.items || []).map(function (i) {
        i.price = Number(i.price);
        return i;
      })
    };
    // Show items grouped in the owner's category order, then by name.
    var catIndex = {};
    state.menu.categories.forEach(function (c, n) { catIndex[c.id] = n; });
    state.menu.items.sort(function (x, y) {
      return (catIndex[x.category_id] - catIndex[y.category_id]) || String(x.name).localeCompare(String(y.name));
    });
    state.itemsById = {};
    state.menu.items.forEach(function (i) { state.itemsById[i.id] = i; });
    state.lastMenuAt = Date.now();
  }
  function loadMenu() {
    return rpc("customer_get_menu", { p_token: state.token }).then(setMenu);
  }

  function renderHeader() {
    var r = state.menu.restaurant;
    $("restName").textContent = r.name || "Menu";
    document.title = (r.name || "Menu") + " - Table " + (state.menu.table.label || "");
    $("tableName").textContent = "Table " + (state.menu.table.label || "");
    var logo = $("logo");
    if (r.logo_url && /^https:\/\//.test(r.logo_url)) { logo.src = r.logo_url; logo.hidden = false; logo.onerror = function () { logo.hidden = true; }; }
    else logo.hidden = true;
  }

  function renderChips() {
    var box = $("chips");
    clear(box);
    var make = function (id, label) {
      box.appendChild(h("button", {
        type: "button", class: "chip", "aria-pressed": String(state.category === id), text: label,
        onclick: function () { state.category = id; renderChips(); renderMenu(); }
      }));
    };
    make("all", "All");
    state.menu.categories.forEach(function (c) { make(c.id, c.name); });
    [["is_kids", "👶 Kids"], ["is_family", "👨‍👩‍👧 Family"], ["is_mild", "🌿 Mild"]].forEach(function (t) {
      if (!state.menu.items.some(function (i) { return i[t[0]]; })) return;
      box.appendChild(h("button", {
        type: "button", class: "chip", "aria-pressed": String(state.tag === t[0]), text: t[1],
        onclick: function () { state.tag = state.tag === t[0] ? "" : t[0]; renderChips(); renderMenu(); }
      }));
    });
  }

  function qtyInCart(id) {
    for (var i = 0; i < state.cart.length; i++) if (state.cart[i].id === id) return state.cart[i].qty;
    return 0;
  }
  function setQty(item, qty) {
    qty = Math.max(0, Math.min(MAX_QTY, qty));
    var idx = -1;
    state.cart.forEach(function (l, i) { if (l.id === item.id) idx = i; });
    if (qty === 0 && idx >= 0) state.cart.splice(idx, 1);
    else if (idx >= 0) state.cart[idx].qty = qty;
    else if (qty > 0) state.cart.push({ id: item.id, name: item.name, qty: qty, note: "", seen_price: Number(item.price) });
    state.cartMessage = "";
    save("cart", state.cart);
    renderMenu(); renderCartBar();
    if (!$("sheet").hidden) renderSheet();
  }

  function stepper(item) {
    var q = qtyInCart(item.id);
    if (!item.is_available) return h("button", { type: "button", class: "secondary", disabled: true, text: "Unavailable" });
    if (q === 0) return h("button", { type: "button", class: "primary", text: "Add", "aria-label": "Add " + item.name, onclick: function () { setQty(item, 1); } });
    return h("div", { class: "stepper" },
      h("button", { type: "button", "aria-label": "Remove one " + item.name, text: "−", onclick: function () { setQty(item, q - 1); } }),
      h("span", { text: String(q), "aria-live": "polite" }),
      h("button", { type: "button", "aria-label": "Add one more " + item.name, text: "+", disabled: q >= MAX_QTY, onclick: function () { setQty(item, q + 1); } }));
  }

  function itemCard(item) {
    var img = item.image_url && /^https:\/\//.test(item.image_url)
      ? h("img", { src: item.image_url, alt: "", loading: "lazy", referrerpolicy: "no-referrer", onerror: function (e) { e.target.style.display = "none"; } }) : null;
    return h("article", { class: "card item" + (img ? " has-img" : "") + (item.is_available ? "" : " unavailable") },
      img,
      h("div", null,
        h("div", null, item.is_featured ? h("span", { class: "badge", text: "⭐ Featured" }) : null, item.is_popular ? h("span", { class: "badge", text: "🔥 Popular" }) : null),
        h("h3", { text: item.name }),
        item.description ? h("p", { class: "desc", text: item.description }) : null,
        Number(item.prep_minutes) > 0 ? h("p", { class: "small", text: "⏱ about " + item.prep_minutes + " min" }) : null),
      h("div", { class: "actions" }, h("span", { class: "price", text: formatPkr(item.price) }), stepper(item)));
  }

  function renderMenu() {
    var box = $("menuList");
    clear(box);
    var q = state.search.trim().toLowerCase();
    var items = state.menu.items.filter(function (i) {
      if (state.category !== "all" && i.category_id !== state.category) return false;
      if (state.tag && !i[state.tag]) return false;
      return !q || (i.name + " " + (i.description || "")).toLowerCase().indexOf(q) >= 0;
    });
    if (!items.length) {
      box.appendChild(h("p", { class: "muted", text: state.menu.items.length ? "Nothing matches your search." : "The menu is empty right now." }));
      return;
    }
    items.forEach(function (i) { box.appendChild(itemCard(i)); });
  }

  // ---- cart ------------------------------------------------------------------------
  function renderCartBar() {
    var count = cartCount(state.cart);
    $("cartBar").hidden = count === 0;
    $("openCart").textContent = "View order · " + count + (count === 1 ? " item" : " items") + " · " + formatPkr(cartTotal(state.cart, state.itemsById));
  }

  function openSheet() { $("sheet").hidden = false; document.body.style.overflow = "hidden"; $("closeSheet").focus(); }
  function closeSheet() { $("sheet").hidden = true; document.body.style.overflow = ""; }

  function renderSheet() {
    var body = $("sheetBody");
    clear(body);
    $("sheetTitle").textContent = "Your order";
    if (!state.cart.length) {
      body.appendChild(h("p", { class: "muted", text: "Your order is empty. Add something from the menu." }));
      return;
    }
    if (state.cartMessage) body.appendChild(h("div", { class: "notice", role: "alert", text: state.cartMessage }));
    var ups = upsellFor(state.menu, state.cart, 2);
    if (ups.length) {
      var up = h("div", { class: "card", style: "margin-bottom:10px" }, h("strong", { text: "Add a drink or dessert?" }));
      ups.forEach(function (u) {
        up.appendChild(h("div", { class: "row", style: "align-items:center;justify-content:space-between;margin-top:6px" },
          h("span", { text: (u.role === "drink" ? "🥤 " : "🍰 ") + u.item.name + " · " + formatPkr(u.item.price) }),
          h("button", { type: "button", class: "secondary", text: "Add", onclick: function () { setQty(u.item, 1); } })));
      });
      body.appendChild(up);
    }
    state.cart.forEach(function (line) {
      var item = state.itemsById[line.id];
      var price = item ? Number(item.price) : line.seen_price;
      body.appendChild(h("div", { class: "line" },
        h("div", null, h("strong", { text: line.name }), h("div", { class: "small", text: formatPkr(price) + " each" })),
        h("div", { class: "row", style: "align-items:center;justify-content:flex-end" },
          h("div", { class: "stepper" },
            h("button", { type: "button", text: "−", "aria-label": "Remove one " + line.name, onclick: function () { setQty({ id: line.id, name: line.name, price: price }, line.qty - 1); } }),
            h("span", { text: String(line.qty) }),
            h("button", { type: "button", text: "+", "aria-label": "Add one more " + line.name, disabled: line.qty >= MAX_QTY, onclick: function () { setQty({ id: line.id, name: line.name, price: price }, line.qty + 1); } })),
          h("strong", { class: "price", text: formatPkr(price * line.qty) })),
        h("div", { class: "note" }, h("input", {
          type: "text", maxlength: "200", placeholder: "Customize (e.g. no onions, extra spicy)", "aria-label": "Note for " + line.name,
          value: line.note || "", oninput: function (e) { line.note = e.target.value; save("cart", state.cart); }
        }))));
    });
    body.appendChild(h("label", { class: "field", for: "orderNote", text: "Note for the kitchen (optional)" }));
    body.appendChild(h("textarea", { id: "orderNote", maxlength: "500", placeholder: "Allergies, serving preferences...", oninput: function (e) { state.orderNote = e.target.value; } }, state.orderNote));
    $("orderNote").value = state.orderNote;
    body.appendChild(h("div", { class: "total" }, h("span", { text: "Total" }), h("span", { text: formatPkr(cartTotal(state.cart, state.itemsById)) })));
    var promise = cartPromise(state.cart, state.itemsById), rest = state.menu.restaurant;
    if (promise) {
      var msg = "⏱ Your order should be served in about " + promise + " minutes.";
      if (Number(rest.late_discount_pct) > 0) msg += " If it is later than that, you get " + Number(rest.late_discount_pct) + "% off (when the bill is still unpaid at serving).";
      body.appendChild(h("div", { class: "notice ok", text: msg }));
    }
    if (Number(rest.cancel_window_minutes) > 0) {
      body.appendChild(h("p", { class: "small", text: "You can cancel within " + rest.cancel_window_minutes + " minutes of ordering, before the kitchen starts preparing." }));
    }
    body.appendChild(h("p", { class: "small", text: "Prices are checked again by the restaurant when you place the order. Pay at the table as usual." }));
    body.appendChild(h("div", { id: "orderError", class: "error", role: "alert" }));
    body.appendChild(h("button", { id: "placeBtn", type: "button", class: "primary wide", text: state.placing ? "Sending..." : "Place order", disabled: state.placing, onclick: placeOrder }));
  }

  function showOrderError(msg) { var e = $("orderError"); if (e) e.textContent = msg || ""; }

  function placeOrder() {
    if (state.placing || !state.cart.length) return;
    state.placing = true;
    renderSheet();
    loadMenu().then(function () {
      var rec = reconcileCart(state.cart, state.menu.items);
      if (rec.removed.length || rec.changed.length) {
        state.cart = rec.cart;
        save("cart", state.cart);
        var parts = [];
        if (rec.removed.length) parts.push("No longer available and removed: " + rec.removed.join(", ") + ".");
        rec.changed.forEach(function (c) { parts.push(c.name + " is now " + formatPkr(c.to) + " (was " + formatPkr(c.from) + ")."); });
        state.cartMessage = parts.join(" ") + " Please review your order and place it again.";
        state.placing = false;
        renderAll();
        return null;
      }
      var sig = cartSignature(state.cart, state.orderNote);
      if (!state.key || state.keySig !== sig) { state.key = randomKey(); state.keySig = sig; }
      var items = state.cart.map(function (l) { return { item_id: l.id, qty: l.qty, note: l.note ? l.note.trim().slice(0, 200) : undefined }; });
      return rpc("customer_place_order", { p_token: state.token, p_items: items, p_notes: state.orderNote.trim() || null, p_idempotency_key: state.key })
        .then(function (res) {
          state.orderIds = [res.order_id].concat(state.orderIds.filter(function (id) { return id !== res.order_id; })).slice(0, 10);
          save("orders", state.orderIds);
          state.cart = []; state.orderNote = ""; state.key = null; state.keySig = ""; state.cartMessage = "";
          save("cart", state.cart);
          state.placing = false;
          renderMenu(); renderCartBar(); showConfirmation(res);
        });
    }).catch(function (err) {
      state.placing = false;
      renderSheet();
      showOrderError(err && err.message ? err.message : "Could not place the order. Please try again.");
    });
  }

  function showConfirmation(res) {
    var body = $("sheetBody");
    clear(body);
    $("sheetTitle").textContent = "Thank you!";
    body.appendChild(h("div", { class: "notice ok" },
      h("strong", { text: "Order #" + res.order_number + " received" }),
      h("div", { text: "Total " + formatPkr(res.total) + " · Table " + res.table_label })));
    body.appendChild(h("p", { text: "Your order was sent to the kitchen. You can follow its progress under “Orders”." }));
    body.appendChild(h("button", { type: "button", class: "primary wide", text: "Track my order", onclick: function () { closeSheet(); switchTab("orders"); } }));
    body.appendChild(h("button", { type: "button", class: "link", text: "Back to menu", onclick: closeSheet }));
  }

  // ---- orders ------------------------------------------------------------------------
  function refreshOrders() {
    if (!state.orderIds.length) { state.orders = []; renderOrders(); return Promise.resolve(); }
    return rpc("customer_get_orders", { p_token: state.token, p_order_ids: state.orderIds })
      .then(function (list) { state.orders = list || []; renderOrders(); })
      .catch(function () { /* keep showing the last known state */ });
  }

  function promiseNote(o) {
    var left = promiseLeft(o, Date.now());
    if (left === null) return null;
    var pct = Number(o.late_discount_pct) || 0;
    if (left >= 0) return h("p", { class: "small", text: "⏱ Promised in " + o.promised_minutes + " min · about " + Math.ceil(left) + " min left" });
    return h("p", { class: "small", text: "⏱ Running late." + (pct > 0 ? " You get " + pct + "% off when it is served (if the bill is unpaid)." : "") });
  }

  function cancelOrder(o) {
    if (!root.confirm("Cancel order #" + o.order_number + "?")) return;
    rpc("customer_cancel_order", { p_token: state.token, p_order_id: o.order_id })
      .then(function () { toast("Order cancelled."); })
      .catch(function (err) { toast(err.message); })
      .then(refreshOrders);
  }

  function renderOrders() {
    var box = $("ordersBody");
    clear(box);
    if (!state.orders.length) {
      box.appendChild(h("p", { class: "muted", text: "Orders you place from this phone will appear here." }));
      return;
    }
    state.orders.forEach(function (o) {
      var idx = STATUS_STEPS.indexOf(o.status);
      var steps = h("div", { class: "steps", "aria-hidden": "true" });
      if (o.status !== "cancelled") STATUS_STEPS.forEach(function (s, i) { steps.appendChild(h("i", { class: i <= idx ? "done" : "" })); });
      box.appendChild(h("article", { class: "card", style: "margin-bottom:10px" },
        h("div", { class: "order-head" }, h("h3", { text: "Order #" + o.order_number }), h("span", { class: "status " + o.status, text: STATUS_TEXT[o.status] || o.status })),
        steps,
        o.paid_at ? h("p", { class: "small", text: "✅ Paid · " + (METHOD_TEXT[o.payment_method] || "") }) : null,
        promiseNote(o),
        h("ul", { class: "plain" }, (o.items || []).map(function (it) { return h("li", null, h("span", { text: it.quantity + " × " + it.name }), h("span", { text: formatPkr(it.line_total) })); })),
        Number(o.discount) > 0 ? h("div", { class: "total" }, h("span", { text: "Late-service discount (" + Number(o.late_discount_pct) + "%)" }), h("span", { text: "− " + formatPkr(o.discount) })) : null,
        h("div", { class: "total" }, h("span", { text: "Total" }), h("span", { text: formatPkr(o.total) })),
        o.cancel_until ? h("button", { type: "button", class: "secondary", style: "margin-top:8px", text: "Cancel this order (until " + new Date(o.cancel_until).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) + ")", onclick: function () { cancelOrder(o); } }) : null));
    });
  }

  // ---- bill and payment ------------------------------------------------------------------
  function refreshPayments() {
    if (!state.paymentIds.length) { state.payments = []; return Promise.resolve(); }
    return rpc("customer_get_payments", { p_token: state.token, p_ids: state.paymentIds })
      .then(function (list) { state.payments = list || []; })
      .catch(function () { /* keep the last known state */ });
  }
  function refreshTips() {
    if (!state.tipIds.length) { state.tips = []; return Promise.resolve(); }
    return rpc("customer_get_tips", { p_token: state.token, p_ids: state.tipIds })
      .then(function (list) { state.tips = list || []; })
      .catch(function () { /* keep the last known state */ });
  }
  function addTip() {
    var amt = Number(state.tipAmt);
    if (!(amt >= 1)) { toast("Enter a tip amount."); return; }
    rpc("customer_add_tip", { p_token: state.token, p_recipient: state.tipTo, p_amount: amt })
      .then(function (r) {
        if (state.tipIds.indexOf(r.tip_id) < 0) { state.tipIds = [r.tip_id].concat(state.tipIds).slice(0, 20); save("tips", state.tipIds); }
        state.tipAmt = ""; toast("Tip added to your bill.");
      })
      .catch(function (err) { toast(err.message); })
      .then(refreshBill);
  }
  function removeTip(id) {
    rpc("customer_remove_tip", { p_token: state.token, p_id: id })
      .then(function () { toast("Tip removed."); })
      .catch(function (err) { toast(err.message); })
      .then(refreshBill);
  }
  function sendFeedback() {
    if (state.fbBusy) return;
    if (!(state.fbRating >= 1)) { toast("Please choose 1 to 5 stars."); return; }
    state.fbBusy = true; renderBill();
    rpc("customer_submit_feedback", { p_token: state.token, p_rating: state.fbRating, p_comment: state.fbComment.trim() || null })
      .then(function () { save("feedback_at", Date.now()); state.fbRating = 0; state.fbComment = ""; toast("Thank you for your feedback!"); })
      .catch(function (err) { toast(err.message); })
      .then(function () { state.fbBusy = false; renderBill(); });
  }
  function refreshBill() {
    return Promise.all([refreshOrders(), refreshPayments(), refreshTips()]).then(function () { if (state.tab === "bill") renderBill(); });
  }

  // Shrinks a screenshot to a small JPEG so it uploads quickly on mobile data.
  function compressImage(file) {
    return new Promise(function (resolve, reject) {
      if (!file || !/^image\//.test(file.type)) { reject(new Error("Please choose a photo or screenshot.")); return; }
      var fr = new FileReader();
      fr.onerror = function () { reject(new Error("Could not read that image.")); };
      fr.onload = function () {
        var img = new Image();
        img.onerror = function () { reject(new Error("That file is not a usable image.")); };
        img.onload = function () {
          var k = Math.min(1, 1280 / Math.max(img.width, img.height));
          var c = document.createElement("canvas");
          c.width = Math.max(1, Math.round(img.width * k)); c.height = Math.max(1, Math.round(img.height * k));
          var g = c.getContext("2d");
          g.fillStyle = "#fff"; g.fillRect(0, 0, c.width, c.height); g.drawImage(img, 0, 0, c.width, c.height);
          var q = 0.8, out = c.toDataURL("image/jpeg", q);
          while (out.length > 650000 && q > 0.3) { q -= 0.1; out = c.toDataURL("image/jpeg", q); }
          if (out.length > 650000) reject(new Error("That image is too large. Please send a smaller screenshot."));
          else resolve(out);
        };
        img.src = fr.result;
      };
      fr.readAsDataURL(file);
    });
  }

  function submitPayment(due) {
    if (state.paying) return;
    var method = state.payMethod;
    if (!method) { toast("Please choose how you want to pay."); return; }
    if (method === "online" && !state.proof) { toast("Please attach your payment screenshot first."); return; }
    state.paying = true; renderBill();
    rpc("customer_create_payment", {
      p_token: state.token, p_order_ids: due.map(function (o) { return o.order_id; }),
      p_method: method, p_proof: method === "online" ? state.proof : null,
      p_tip_ids: openTips(state.tips).map(function (t) { return t.tip_id; })
    }).then(function (r) {
      state.paymentIds = [r.payment_id].concat(state.paymentIds).slice(0, 10);
      save("payments", state.paymentIds);
      state.payMethod = ""; state.proof = null;
      toast("Sent. Your service provider will confirm it.");
    }).catch(function (err) { toast(err.message); })
      .then(function () { state.paying = false; return refreshBill(); });
  }

  function cancelPayment(id) {
    rpc("customer_cancel_payment", { p_token: state.token, p_id: id })
      .then(function () { toast("Payment request cancelled."); })
      .catch(function (err) { toast(err.message); })
      .then(refreshBill);
  }

  function whenText(iso) { try { return new Date(iso).toLocaleString(); } catch (e) { return ""; } }

  function renderBill() {
    var box = $("billBody");
    clear(box);
    var live = billOrders(state.orders);
    if (!live.length) {
      box.appendChild(h("p", { class: "muted", text: "Your bill will appear here after you place an order." }));
      return;
    }
    var due = payableOrders(state.orders, state.payments);
    var pending = state.payments.filter(function (p) { return p.status === "pending"; });

    pending.forEach(function (p) {
      box.appendChild(h("div", { class: "notice" },
        h("strong", { text: "⏳ Waiting for confirmation" }),
        h("p", { class: "small", style: "margin:4px 0 8px", text: (METHOD_TEXT[p.method] || p.method) + " · " + formatPkr(p.amount) + ". Your service provider will confirm it shortly." }),
        h("button", { type: "button", class: "secondary", text: "Cancel this payment", onclick: function () { cancelPayment(p.payment_id); } })));
    });

    var rejected = state.payments.filter(function (p) { return p.status === "rejected"; })[0];
    if (rejected && due.length) {
      box.appendChild(h("div", { class: "notice" }, h("strong", { text: "Your last payment was not accepted." }),
        h("p", { class: "small", style: "margin:4px 0 0", text: (rejected.reject_reason ? rejected.reject_reason + " " : "") + "Please try again or call your service provider." })));
    }

    if (due.length) {
      box.appendChild(h("h3", { class: "section-title", text: "To pay" }));
      var card = h("article", { class: "card" });
      due.forEach(function (o) {
        card.appendChild(h("div", { class: "line" }, h("span", { text: "Order #" + o.order_number + " · " + STATUS_TEXT[o.status] }), h("span", { text: formatPkr(o.total) })));
      });
      var openT = openTips(state.tips), tipSum = sumAmounts(openT);
      var payTotal = Math.round((sumTotals(due) + tipSum) * 100) / 100;
      openT.forEach(function (t) {
        card.appendChild(h("div", { class: "line" }, h("span", { text: "💝 Tip · " + tipLabel(t) }), h("span", { text: formatPkr(t.amount) }),
          h("button", { type: "button", class: "link", text: "Remove", onclick: function () { removeTip(t.tip_id); } })));
      });
      card.appendChild(h("div", { class: "total" }, h("span", { text: "Total to pay" }), h("span", { text: formatPkr(payTotal) })));
      box.appendChild(card);
      box.appendChild(tipBox(live));

      var grid = h("div", { class: "pay-grid" });
      [["online", "📲 Pay online (send screenshot)"], ["cash", "💵 Pay cash"], ["card", "💳 Pay by card"]].forEach(function (m) {
        grid.appendChild(h("button", { type: "button", "aria-pressed": String(state.payMethod === m[0]), text: m[1], onclick: function () {
          state.payMethod = m[0]; state.proof = null; renderBill();
        } }));
      });
      box.appendChild(grid);

      if (state.payMethod === "online") {
        var info = state.menu.restaurant.payment_instructions;
        box.appendChild(h("div", { class: "card", style: "margin-top:10px" },
          h("strong", { text: "1. Send " + formatPkr(payTotal) + " to the restaurant" }),
          h("p", { class: "pre", text: info || "Ask your service provider for the account details." }),
          h("strong", { text: "2. Attach the screenshot of your payment" }),
          h("input", { type: "file", accept: "image/*", style: "margin-top:8px", onchange: function (e) {
            compressImage(e.target.files && e.target.files[0]).then(function (url) { state.proof = url; renderBill(); })
              .catch(function (err) { toast(err.message); });
          } }),
          state.proof ? h("img", { class: "proof-preview", src: state.proof, alt: "Your payment screenshot" }) : null));
      } else if (state.payMethod === "cash") {
        box.appendChild(h("div", { class: "notice ok" }, "Keep " + formatPkr(payTotal) + " ready. Your service provider will come to collect it and confirm the payment."));
      } else if (state.payMethod === "card") {
        box.appendChild(h("div", { class: "notice ok" }, "Your service provider will bring the card machine. Confirm below and they will come to your table."));
      }
      if (state.payMethod) {
        box.appendChild(h("button", { type: "button", class: "primary wide", style: "margin-top:12px", disabled: state.paying || (state.payMethod === "online" && !state.proof),
          text: state.paying ? "Sending..." : (state.payMethod === "online" ? "Send payment proof" : "Tell my service provider"),
          onclick: function () { submitPayment(due); } }));
      }
    } else if (!pending.length) {
      box.appendChild(h("div", { class: "notice ok", text: "✅ Everything is paid. Thank you!" }));
    }

    var paid = live.filter(function (o) { return o.paid_at; });
    if (paid.length) {
      box.appendChild(h("h3", { class: "section-title", text: "Paid" }));
      var pc = h("article", { class: "card" });
      paid.forEach(function (o) {
        pc.appendChild(h("div", { class: "line" },
          h("span", { text: "Order #" + o.order_number }),
          h("span", { text: formatPkr(o.total) }),
          h("span", { class: "small note", text: "✅ Payment received · " + (METHOD_TEXT[o.payment_method] || "") + " · " + whenText(o.paid_at) })));
      });
      box.appendChild(pc);
    }
    var rcv = (state.tips || []).filter(function (t) { return t.status === "received"; });
    if (rcv.length) {
      var tc = h("article", { class: "card", style: "margin-top:10px" });
      rcv.forEach(function (t) {
        tc.appendChild(h("div", { class: "line" }, h("span", { text: "💝 Tip · " + tipLabel(t) }), h("span", { text: formatPkr(t.amount) }),
          h("span", { class: "small note", text: "✅ Received · thank you!" })));
      });
      box.appendChild(tc);
    }
    box.appendChild(h("button", { type: "button", class: "secondary wide", style: "margin-top:14px;width:100%", text: "🧾 View and save bill (PDF)", onclick: showBill }));
    box.appendChild(feedbackBox());
  }

  function tipBox(live) {
    var prov = "";
    live.forEach(function (o) { if (!prov && o.waiter_name) prov = o.waiter_name; });
    var wrap = h("div", { class: "card", style: "margin-top:10px" }, h("strong", { text: "💝 Add a tip (optional)" }));
    var who = h("div", { class: "pay-grid", style: "margin-top:8px" });
    [["waiter", "Service provider" + (prov ? " (" + prov + ")" : "")], ["kitchen", "👨‍🍳 Kitchen"]].forEach(function (r) {
      who.appendChild(h("button", { type: "button", "aria-pressed": String(state.tipTo === r[0]), text: r[1], onclick: function () { state.tipTo = r[0]; renderBill(); } }));
    });
    wrap.appendChild(who);
    var amts = h("div", { class: "pay-grid", style: "margin-top:8px" });
    [50, 100, 200].forEach(function (a) {
      amts.appendChild(h("button", { type: "button", "aria-pressed": String(String(state.tipAmt) === String(a)), text: "Rs. " + a, onclick: function () { state.tipAmt = String(a); renderBill(); } }));
    });
    wrap.appendChild(amts);
    wrap.appendChild(h("input", { type: "number", min: "1", max: "100000", step: "10", inputmode: "numeric", placeholder: "Other amount (Rs.)", style: "margin-top:8px", value: state.tipAmt,
      oninput: function (e) { state.tipAmt = e.target.value; } }));
    wrap.appendChild(h("button", { type: "button", class: "secondary", style: "margin-top:8px", text: "Add tip to my bill", onclick: addTip }));
    wrap.appendChild(h("p", { class: "small", text: "Tips are paid together with your bill. They are not part of the food bill." }));
    return wrap;
  }

  function feedbackBox() {
    var sent = Number(load("feedback_at", 0)) || 0;
    if (sent && Date.now() - sent < 3 * 3600 * 1000) {
      return h("div", { class: "notice ok", style: "margin-top:14px", text: "⭐ Thank you for your feedback!" });
    }
    var stars = h("div", { class: "pay-grid", style: "margin-top:8px" });
    [1, 2, 3, 4, 5].forEach(function (n) {
      stars.appendChild(h("button", { type: "button", "aria-label": n + " star" + (n > 1 ? "s" : ""), "aria-pressed": String(state.fbRating === n), text: (state.fbRating >= n ? "★" : "☆") + " " + n, onclick: function () { state.fbRating = n; renderBill(); } }));
    });
    return h("div", { class: "card", style: "margin-top:14px" },
      h("strong", { text: "How was your visit?" }), stars,
      h("textarea", { maxlength: "500", placeholder: "Tell us more (optional)", style: "margin-top:8px", oninput: function (e) { state.fbComment = e.target.value; } }, state.fbComment),
      h("button", { type: "button", class: "primary", style: "margin-top:8px", disabled: state.fbBusy, text: state.fbBusy ? "Sending..." : "Send feedback", onclick: sendFeedback }));
  }

  // ---- info sheet: bill and terms -----------------------------------------------------------
  function openInfo(title, nodes) {
    $("infoTitle").textContent = title;
    var b = $("infoBody"); clear(b);
    [].concat(nodes).forEach(function (n) { if (n) b.appendChild(n); });
    $("infoSheet").hidden = false;
  }
  function closeInfo() { $("infoSheet").hidden = true; }

  function showTerms() {
    var t = state.menu && state.menu.restaurant.terms;
    openInfo("Terms & Conditions", h("p", { class: "pre", text: t || "This restaurant has not added its terms and conditions yet." }));
  }

  function showBill() {
    var r = state.menu.restaurant, live = billOrders(state.orders), stt = billState(state.orders);
    var seen = {}, providers = [];
    live.forEach(function (o) { if (o.waiter_name && !seen[o.waiter_name]) { seen[o.waiter_name] = 1; providers.push(o.waiter_name); } });
    var paid = live.filter(function (o) { return o.paid_at; });
    var methods = [], mseen = {};
    paid.forEach(function (o) { var m = METHOD_TEXT[o.payment_method]; if (m && !mseen[m]) { mseen[m] = 1; methods.push(m); } });
    var lastPaid = paid.map(function (o) { return o.paid_at; }).sort().slice(-1)[0];
    var table = h("table");
    live.slice().reverse().forEach(function (o) {
      table.appendChild(h("tr", null, h("td", { colspan: "2" }, h("strong", { text: "Order #" + o.order_number + " · " + whenText(o.created_at) }))));
      (o.items || []).forEach(function (it) {
        table.appendChild(h("tr", null, h("td", { text: it.quantity + " × " + it.name }), h("td", { text: formatPkr(it.line_total) })));
      });
      if (Number(o.discount) > 0) table.appendChild(h("tr", null, h("td", { text: "Late-service discount (" + Number(o.late_discount_pct) + "%)" }), h("td", { text: "− " + formatPkr(o.discount) })));
    });
    var billTips = state.tips || [];
    billTips.forEach(function (t) {
      table.appendChild(h("tr", null, h("td", { text: "Tip · " + tipLabel(t) + (t.status === "received" ? " (received)" : "") }), h("td", { text: formatPkr(t.amount) })));
    });
    var tipTotal = sumAmounts(billTips);
    var stamp = stt === "paid" ? h("span", { class: "stamp", text: "PAYMENT RECEIVED" })
      : stt === "partial" ? h("span", { class: "stamp unpaid", text: "PARTLY PAID" })
      : h("span", { class: "stamp unpaid", text: "NOT PAID YET" });
    var logo = r.logo_url && /^https:\/\//.test(r.logo_url) ? h("img", { class: "logo", src: r.logo_url, alt: "", referrerpolicy: "no-referrer" }) : null;
    var paidTotal = sumTotals(paid);
    var node = h("div", { class: "bill" },
      h("div", { class: "bill-head" }, logo, h("div", null, h("h2", { text: r.name || "Restaurant" }), r.phone ? h("p", { class: "small", text: r.phone }) : null)),
      h("p", { class: "small", text: "Bill · " + new Date().toLocaleString() }),
      h("p", { text: "Table " + (state.menu.table.label || "") + (providers.length ? " · Service provider: " + providers.join(", ") : "") }),
      h("hr"), table, h("hr"),
      h("div", { class: "total" }, h("span", { text: tipTotal > 0 ? "Food total" : "Total" }), h("span", { text: formatPkr(sumTotals(live)) })),
      tipTotal > 0 ? h("div", { class: "total" }, h("span", { text: "Total with tip" }), h("span", { text: formatPkr(sumTotals(live) + tipTotal) })) : null,
      stt === "partial" ? h("p", { class: "small", text: "Paid " + formatPkr(paidTotal) + " · Still to pay " + formatPkr(sumTotals(live) - paidTotal) }) : null,
      h("p", null, stamp),
      paid.length ? h("p", { class: "small", text: "Paid by " + methods.join(", ") + (lastPaid ? " on " + whenText(lastPaid) : "") }) : null,
      h("p", { class: "small", text: "Thank you for dining with us." }),
      h("div", { class: "row no-print", style: "margin-top:12px" },
        h("button", { type: "button", class: "primary", text: "Save as PDF / Print", onclick: function () {
          var old = document.title;
          document.title = (r.name || "Restaurant") + " - Bill";
          root.print();
          setTimeout(function () { document.title = old; }, 1500);
        } }),
        h("button", { type: "button", class: "secondary", text: "Close", onclick: closeInfo })),
      h("p", { class: "small no-print", text: "Tip: in the print window choose “Save as PDF” as the printer." }));
    openInfo("Your bill", node);
  }

  // ---- help / requests -------------------------------------------------------------
  function refreshRequests() {
    return rpc("customer_get_requests", { p_token: state.token })
      .then(function (list) { state.requests = list || []; renderHelp(); })
      .catch(function () { /* ignore */ });
  }

  function sendRequest(type, note) {
    return rpc("customer_create_request", { p_token: state.token, p_type: type, p_note: note || null })
      .then(function (r) { toast(r.duplicate ? "A service provider has already been notified." : "A service provider has been notified."); return refreshRequests(); })
      .catch(function (err) { toast(err.message); });
  }

  function renderHelp() {
    var box = $("helpBody");
    var keep = $("otherNote") ? $("otherNote").value : "";
    clear(box);
    box.appendChild(h("p", { class: "muted", text: "Tap a button and a service provider will come to your table." }));
    var grid = h("div", { class: "help-grid", style: "margin-top:10px" });
    REQUEST_TYPES.forEach(function (rt) {
      if (rt[0] === "other") return;
      grid.appendChild(h("button", { type: "button", text: rt[1], onclick: function () { sendRequest(rt[0]); } }));
    });
    box.appendChild(grid);
    box.appendChild(h("label", { class: "field", for: "otherNote", text: "Something else?" }));
    box.appendChild(h("input", { id: "otherNote", type: "text", maxlength: "200", placeholder: "Tell us what you need", value: keep }));
    box.appendChild(h("button", { type: "button", class: "secondary", style: "margin-top:8px", text: "Send request", onclick: function () {
      var note = $("otherNote").value.trim();
      if (!note) { toast("Please type what you need."); return; }
      sendRequest("other", note).then(function () { var n = $("otherNote"); if (n) n.value = ""; });
    } }));
    box.appendChild(h("h2", { class: "section-title", text: "Your requests" }));
    if (!state.requests.length) box.appendChild(h("p", { class: "muted", text: "No requests yet." }));
    state.requests.forEach(function (r) {
      var label = (REQUEST_TYPES.filter(function (t) { return t[0] === r.request_type; })[0] || [0, r.request_type])[1];
      box.appendChild(h("div", { class: "card", style: "margin-bottom:8px" },
        h("div", { class: "order-head" }, h("strong", { text: label }), h("span", { class: "status " + (r.status === "cancelled" ? "cancelled" : ""), text: REQUEST_STATUS_TEXT[r.status] || r.status }))));
    });
  }

  // ---- suggestions (basic and optional AI) -----------------------------------------------
  function addSuggestionToCart(list) {
    list.forEach(function (l) {
      var item = state.itemsById[l.id || (l.item && l.item.id)];
      if (!item || !item.is_available) return;
      var cur = qtyInCart(item.id);
      setQty(item, cur + (l.qty || 1));
    });
    toast("Added to your order.");
  }

  function suggestionList(lines, total, onAdd) {
    var box = h("div", { class: "card", style: "margin-top:10px" });
    box.appendChild(h("ul", { class: "plain" }, lines.map(function (l) {
      return h("li", null, h("span", { text: l.qty + " × " + l.item.name }), h("span", { text: formatPkr(Number(l.item.price) * l.qty) }));
    })));
    box.appendChild(h("div", { class: "total" }, h("span", { text: "Estimated total" }), h("span", { text: formatPkr(total) })));
    box.appendChild(h("button", { type: "button", class: "primary wide", text: "Add all to my order", onclick: onAdd }));
    return box;
  }

  function renderSuggest() {
    var box = $("suggestBody");
    var prev = {
      people: $("sgPeople") ? $("sgPeople").value : "2", budget: $("sgBudget") ? $("sgBudget").value : "",
      prefer: $("sgPrefer") ? $("sgPrefer").value : "any", ask: $("aiAsk") ? $("aiAsk").value : ""
    };
    clear(box);
    box.appendChild(h("h2", { class: "section-title", text: "Build me a meal" }));
    box.appendChild(h("div", { class: "row" },
      h("div", null, h("label", { class: "field", for: "sgPeople", text: "People" }), h("input", { id: "sgPeople", type: "number", min: "1", max: "20", value: prev.people })),
      h("div", null, h("label", { class: "field", for: "sgBudget", text: "Budget (Rs.)" }), h("input", { id: "sgBudget", type: "number", min: "0", step: "50", placeholder: "optional", value: prev.budget }))));
    box.appendChild(h("label", { class: "field", for: "sgPrefer", text: "Preference" }));
    var sel = h("select", { id: "sgPrefer" }, h("option", { value: "any", text: "Anything" }), h("option", { value: "spicy", text: "Spicy" }), h("option", { value: "veg", text: "Vegetarian (best guess)" }));
    sel.value = prev.prefer;
    box.appendChild(sel);
    box.appendChild(h("button", { type: "button", class: "primary wide", style: "margin-top:12px", text: "Suggest a meal", onclick: function () {
      state.suggestion = suggestMeal(state.menu, { people: $("sgPeople").value, budget: $("sgBudget").value, prefer: $("sgPrefer").value });
      renderSuggest();
    } }));
    var s = state.suggestion;
    if (s) {
      if (s.ok) {
        box.appendChild(suggestionList(s.items, s.total, function () { addSuggestionToCart(s.items.map(function (l) { return { id: l.item.id, qty: l.qty }; })); }));
        box.appendChild(h("p", { class: "small", text: "Suggestions use the current menu and prices. Please check ingredients and allergens with the staff." }));
      } else if (s.reason === "budget") {
        box.appendChild(h("div", { class: "notice", text: "We could not fit a meal for that budget. The smallest meal we could build costs about " + formatPkr(s.minimum) + "." }));
      } else {
        box.appendChild(h("div", { class: "notice", text: "We could not find enough items for that. Please browse the menu." }));
      }
    }

    var pop = popularItems(state.menu, 6);
    if (pop.length) {
      box.appendChild(h("h2", { class: "section-title", text: "Popular and featured" }));
      pop.forEach(function (i) { box.appendChild(itemCard(i)); });
    }

    if (CFG.ENABLE_AI) {
      box.appendChild(h("h2", { class: "section-title", text: "Ask our assistant" }));
      box.appendChild(h("label", { class: "field", for: "aiAsk", text: "For example: a spicy meal for two under Rs. 1500" }));
      box.appendChild(h("textarea", { id: "aiAsk", maxlength: "300", placeholder: "What are you in the mood for?" }, prev.ask));
      $("aiAsk").value = prev.ask;
      box.appendChild(h("button", { type: "button", class: "secondary wide", style: "margin-top:8px", disabled: state.aiBusy, text: state.aiBusy ? "Thinking..." : "Ask", onclick: askAI }));
      var r = state.aiResult;
      if (r) {
        box.appendChild(h("div", { class: "notice" + (r.ok ? " ok" : ""), text: r.message }));
        if (r.ok && r.lines.length) {
          box.appendChild(suggestionList(r.lines, r.total, function () { addSuggestionToCart(r.lines.map(function (l) { return { id: l.item.id, qty: l.qty }; })); }));
          box.appendChild(h("p", { class: "small", text: "AI suggestions can be wrong. Please confirm ingredients and allergens with the staff." }));
        }
      }
    }
  }

  function askAI() {
    var text = ($("aiAsk").value || "").trim();
    if (!text || state.aiBusy) return;
    state.aiBusy = true; state.aiResult = null; renderSuggest();
    var m = text.match(/(?:rs\.?|pkr|under|below|within)\s*([0-9][0-9,]{1,7})/i);
    var budget = m ? Number(m[1].replace(/,/g, "")) : null;
    post(String(CFG.SUPABASE_URL).replace(/\/+$/, "") + "/functions/v1/ai-assistant", { action: "recommend", token: state.token, prompt: text, budget: budget })
      .then(function (res) {
        var lines = [];
        var total = 0;
        (res.items || []).forEach(function (it) {
          var item = state.itemsById[it.id];
          if (item && item.is_available) { var q = clampInt(it.qty, 1, 10, 1); lines.push({ item: item, qty: q }); total += Number(item.price) * q; }
        });
        if (res.ok && lines.length) state.aiResult = { ok: true, message: String(res.message || "Here is a suggestion.").slice(0, 500), lines: lines, total: Math.round(total * 100) / 100 };
        else throw new Error("fallback");
      })
      .catch(function () {
        // AI is off, busy or failed: fall back to the built-in suggestions.
        var s = suggestMeal(state.menu, { people: 2, budget: null, prefer: /spic|hot/i.test(text) ? "spicy" : "any" });
        state.aiResult = s.ok
          ? { ok: true, message: "The assistant is not available right now, so here is a simple suggestion for two.", lines: s.items, total: s.total }
          : { ok: false, message: "The assistant is not available right now. Please browse the menu.", lines: [], total: 0 };
      })
      .then(function () { state.aiBusy = false; renderSuggest(); });
  }

  // ---- tabs, polling, start-up ------------------------------------------------------------
  function switchTab(tab) {
    state.tab = tab;
    ["menu", "suggest", "orders", "bill", "help"].forEach(function (t) {
      $("view-" + t).hidden = t !== tab;
      $("tab-" + t).setAttribute("aria-selected", String(t === tab));
    });
    if (tab === "orders") refreshOrders();
    if (tab === "bill") { renderBill(); refreshBill(); }
    if (tab === "help") refreshRequests();
    if (tab === "suggest") renderSuggest();
  }

  function renderAll() { renderHeader(); renderChips(); renderMenu(); renderCartBar(); if (!$("sheet").hidden) renderSheet(); }

  function tick() {
    if (document.hidden || state.placing) return;
    // Do not redraw the bill while the customer is typing a tip or a comment.
    var ae = document.activeElement;
    if (state.tab === "bill" && ae && (ae.tagName === "INPUT" || ae.tagName === "TEXTAREA")) return;
    if (state.tab === "orders") refreshOrders();
    else if (state.tab === "bill") refreshBill();
    else if (state.tab === "help") refreshRequests();
    else if (state.tab === "menu" && Date.now() - state.lastMenuAt > 90000 && $("sheet").hidden) {
      loadMenu().then(function () {
        var rec = reconcileCart(state.cart, state.menu.items);
        if (rec.removed.length || rec.changed.length) {
          state.cart = rec.cart; save("cart", state.cart);
          state.cartMessage = "The menu changed: " + rec.removed.map(function (n) { return n + " was removed"; }).concat(rec.changed.map(function (c) { return c.name + " is now " + formatPkr(c.to); })).join("; ") + ".";
          toast("The menu was updated. Please review your order.");
        }
        renderAll();
      }).catch(function () { /* keep the current menu */ });
    }
  }

  function start() {
    var token = new URLSearchParams(root.location.search).get("t") || "";
    if (!validToken(token)) {
      fatal("Please scan the QR code", "Open the camera on your phone and scan the QR code on your table to see the menu.");
      return;
    }
    if (!CFG.SUPABASE_URL || /YOUR-PROJECT/.test(CFG.SUPABASE_URL) || !CFG.SUPABASE_ANON_KEY || /YOUR-ANON/.test(CFG.SUPABASE_ANON_KEY)) {
      fatal("Not set up yet", "The restaurant has not finished setting up this page (config.js).");
      return;
    }
    state.token = token;
    loadMenu().then(function () {
      state.cart = (load("cart", []) || []).filter(function (l) { return l && typeof l.id === "string" && l.qty > 0; });
      var rec = reconcileCart(state.cart, state.menu.items);
      state.cart = rec.cart;
      if (rec.removed.length || rec.changed.length) state.cartMessage = "Your saved order was updated to match today's menu.";
      state.orderIds = (load("orders", []) || []).filter(function (id) { return typeof id === "string"; }).slice(0, 10);
      state.paymentIds = (load("payments", []) || []).filter(function (id) { return typeof id === "string"; }).slice(0, 10);
      state.tipIds = (load("tips", []) || []).filter(function (id) { return typeof id === "string"; }).slice(0, 20);
      $("fatal").hidden = true;
      $("app").hidden = false;
      renderAll();
      renderHelp();
      if (state.orderIds.length) refreshOrders();
    }).catch(function (err) {
      var msg = err && err.message ? err.message : "Please try again.";
      fatal("Menu not available", msg, start);
    });
  }

  document.querySelectorAll(".tabs button").forEach(function (b) { b.addEventListener("click", function () { switchTab(b.getAttribute("data-tab")); }); });
  $("search").addEventListener("input", function (e) { state.search = e.target.value; renderMenu(); });
  $("openCart").addEventListener("click", function () { renderSheet(); openSheet(); });
  $("closeSheet").addEventListener("click", closeSheet);
  $("closeInfo").addEventListener("click", closeInfo);
  $("openTerms").addEventListener("click", showTerms);
  $("infoSheet").addEventListener("click", function (e) { if (e.target === $("infoSheet")) closeInfo(); });
  $("sheet").addEventListener("click", function (e) { if (e.target === $("sheet")) closeSheet(); });
  document.addEventListener("keydown", function (e) { if (e.key === "Escape") { if (!$("sheet").hidden) closeSheet(); if (!$("infoSheet").hidden) closeInfo(); } });
  setInterval(tick, 8000);
  start();
})(typeof window !== "undefined" ? window : globalThis);
