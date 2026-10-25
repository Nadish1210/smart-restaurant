# SMART RESTAURANT
AI-Powered QR Ordering & Intelligent Restaurant Management System

A multi-restaurant SaaS. Customers scan a QR code on their table, browse the menu, order and call the waiter. Owners and waiters manage orders in a dashboard. A Super Admin manages restaurants and subscriptions.

This guide assumes you are a beginner. Follow the steps in order. Nothing here asks you to publish a password or a secret key.

---

## 1. What you are deploying (three pieces)

| Piece | What it is | Where it runs | Keys it may hold |
|---|---|---|---|
| **Dashboard** | `app.py`, `frontend.py`, `backend.py` (Streamlit) for Super Admin, Owner, Waiter | Streamlit Community Cloud | Supabase URL and **anon** key only |
| **Customer page** | Static files in `customer/` (opened by scanning a QR code) | Netlify (or any static host) | Supabase URL and **anon** key only |
| **Supabase** | Database, login, storage, and two Edge Functions | supabase.com | The service-role key lives **only here**, inside the Edge Function runtime. You never copy it anywhere. |

The "anon" key is designed to be public. Security comes from Row Level Security and database functions in `database.sql`, not from hiding the key.

---

## 2. Where every file belongs

Put this exact structure in one GitHub repository:

```
smart-restaurant/
├── app.py                      Streamlit entry point
├── frontend.py                 All dashboard screens
├── backend.py                  All data access and rules
├── database.sql                Run once in the Supabase SQL Editor
├── requirements.txt            Python packages (Streamlit Cloud reads this)
├── netlify.toml                Tells Netlify to publish the customer/ folder
├── .gitignore                  Keeps secrets out of GitHub
├── README.md                   This file
├── .streamlit/
│   ├── config.toml             Theme settings
│   └── secrets.toml.example    Template ONLY. The real secrets.toml is never uploaded.
├── customer/                   The QR ordering page (published by Netlify)
│   ├── index.html
│   ├── app.js
│   ├── style.css
│   ├── config.js               You edit 2 values here (both public-safe)
│   └── _headers                Security headers
├── supabase/
│   └── functions/
│       ├── admin-users/index.ts
│       └── ai-assistant/index.ts
└── tests/
    ├── test_backend.py
    ├── customer_core.test.js
    ├── edge_functions.test.js
    ├── supabase_stubs.sql      TEST DATABASES ONLY
    └── test_database.py        Database tests (throwaway local Postgres only)
```

Files starting with a dot (`.gitignore`, `.streamlit/`) are hidden on some computers. Turn on "show hidden files" before uploading.

---

## 3. Step by step

### Step 1: GitHub
1. Create a free account at github.com and click **New repository**. Name it `smart-restaurant`. Choose **Private** (recommended).
2. Click **uploading an existing file** and drag in all files and folders from section 2. Commit.
3. Check that no file named `secrets.toml` is in the repository.

### Step 2: Supabase project
1. Create an account at supabase.com and click **New project**. Pick a region near your users and a strong database password. Save that password in a password manager, not in GitHub.
2. Open **SQL Editor → New query**, paste the whole of `database.sql`, click **Run**. It is safe to run again later (it is written to be re-runnable).
3. Open **Project Settings → API** and note: the **Project URL** and the **anon / publishable** key. Do not use or copy the `service_role` / secret key anywhere.

### Step 3: Supabase Auth settings
Open **Authentication**:
1. **Sign In / Providers → Email**: turn **off** "Allow new users to sign up". Accounts are created only by your Edge Functions.
2. **URL Configuration**: set **Site URL** to your dashboard address (you get it in Step 6, so come back to this).
3. **Email Templates → Reset Password**: change the link so it reads
   `{{ .SiteURL }}?token_hash={{ .TokenHash }}&type=recovery`
   The dashboard reads that link to let people set a new password.
4. Supabase's built-in email sender is heavily rate-limited (only a few emails per hour). For real use, add your own SMTP provider under Authentication → SMTP Settings.

### Step 4: Create the first Super Admin
1. **Authentication → Users → Add user → Create new user.** Enter your email and a strong password. Tick "Auto Confirm User".
2. In the SQL Editor run (with your email):
   ```sql
   select public.bootstrap_super_admin('you@example.com');
   ```
   It works only while no Super Admin exists yet.

### Step 5: Edge Functions
Two functions need the service-role key (create accounts) or the AI key. Supabase injects `SUPABASE_URL`, `SUPABASE_ANON_KEY` and `SUPABASE_SERVICE_ROLE_KEY` into them automatically. You do not paste those anywhere.

**Option A, dashboard (no tools needed):**
1. **Edge Functions → Deploy a new function → Via Editor.**
2. Name it exactly `admin-users`, paste the contents of `supabase/functions/admin-users/index.ts`, and deploy.
3. Repeat with the name `ai-assistant` and `supabase/functions/ai-assistant/index.ts`.
4. For **both** functions open the function's settings and switch **Verify JWT with legacy secret** (the "Enforce JWT verification" option) **OFF**. The functions check the caller themselves (token verified, role looked up in the database, privileges re-checked inside SQL). If you leave it on, calls may be rejected before the function runs.

**Option B, command line:**
```
npm i -g supabase
supabase login
supabase link --project-ref YOUR-PROJECT-REF
supabase functions deploy admin-users --no-verify-jwt
supabase functions deploy ai-assistant --no-verify-jwt
```

**Optional AI (Gemini):** AI is optional and the app works fully without it.
1. Create a key in Google AI Studio.
2. In Supabase: **Edge Functions → Secrets → add `GEMINI_API_KEY`**. (CLI: `supabase secrets set GEMINI_API_KEY=...`.) Never put this key in GitHub, `config.js` or Streamlit secrets.
3. In the dashboard: Super Admin → AI settings → switch on and press the test button.
4. In `customer/config.js` set `ENABLE_AI: true` (Step 7).

### Step 6: Dashboard on Streamlit Community Cloud
1. Go to share.streamlit.io, sign in with GitHub, click **Create app**, choose your repository, branch `main`, main file `app.py`.
2. Open **Advanced settings → Secrets** and paste (use your real values):
   ```toml
   SUPABASE_URL = "https://YOUR-PROJECT-REF.supabase.co"
   SUPABASE_ANON_KEY = "YOUR-ANON-PUBLIC-KEY"
   CUSTOMER_APP_URL = "https://your-customer-site.netlify.app"
   APP_URL = "https://your-dashboard.streamlit.app"
   ```
   `CUSTOMER_APP_URL` is known after Step 7. You can deploy first with a placeholder and edit the secret afterwards.
3. Deploy. The app refuses to start if you paste a service-role key by mistake.
4. Go back to Supabase → Authentication → URL Configuration and set the **Site URL** to your dashboard address.

### Step 7: Customer page on Netlify
1. Edit `customer/config.js` in GitHub: put your Project URL and **anon** key. `ENABLE_AI` stays `false` unless you did the AI step.
2. At netlify.com choose **Add new site → Import from Git**, pick the repository. `netlify.toml` already sets the publish folder to `customer`. No build command is needed.
3. Copy the site address into the Streamlit secret `CUSTOMER_APP_URL` (Step 6). Reboot the dashboard app.
4. Any static host works if it serves `customer/` and applies the headers in `customer/_headers`. The page puts the table token in the URL, so the headers include `Referrer-Policy: no-referrer`.

### Step 8: First restaurant and QR codes
1. Sign in to the dashboard as the Super Admin. Create a restaurant (owner name, email, temporary password). Choose a plan.
2. Sign in as the Owner (use a private window). Add menu categories and items, add tables, then download each table's QR code and print it.
3. Create waiters from the Owner's staff screen.
4. **Give every table a waiter** (Tables page, or the waiter's own card in Staff). A waiter can look after many tables. Orders and requests from a table go to that table's waiter only.
5. Scan a QR code with a real phone, place an order, and watch it appear in the dashboard (it refreshes about every 10 seconds).

---

## 4. Testing checklist (do this yourself)

Tick each item after you see it work.

**Login and roles**
- [ ] Super Admin, Owner and Waiter each see only their own menu pages.
- [ ] A wrong password shows a clear error.
- [ ] "Forgot password" email arrives and the link opens the "set new password" screen.
- [ ] A deactivated user cannot sign in or keeps no access.

**Customer ordering**
- [ ] The QR opens the menu with the right restaurant and table.
- [ ] An edited token in the URL shows an "invalid QR" screen.
- [ ] Unavailable dishes cannot be ordered.
- [ ] Double-tapping "Place order" creates only one order.
- [ ] If the Owner changes a price after the item is in the cart, the customer is told before ordering.
- [ ] "Call waiter / bill / water" requests show up for staff.
- [ ] The Orders tab shows status changes.

**Table-wise waiters**
- [ ] Each waiter sees only the orders and requests of their own tables, and nothing else.
- [ ] A table with no waiter shows a warning to the Owner, and its orders cannot be advanced until a waiter is assigned.
- [ ] If the Owner (or kitchen) moves an order along, the commission still goes to the table's waiter.
- [ ] "Move all tables" (waiter on leave) hands every table, and its open orders and requests, to another waiter. Completed orders keep their original waiter and commission.
- [ ] Deactivating a waiter frees their tables (they show as "no waiter").

**Staff flow**
- [ ] A new order appears within about 10 seconds. Status moves Pending → Preparing → Ready → Served → Completed.
- [ ] Invalid jumps (for example Pending → Completed) are refused.
- [ ] Completing an order records revenue, and the table's waiter is credited.
- [ ] A refund reverses revenue and commission.

**Tenant isolation (important)**
Create two restaurants, A and B, each with an owner and a waiter.
- [ ] Owner A cannot see B's menu, orders, staff, tables or expenses in any screen.
- [ ] A QR code of A never shows B's menu.
- [ ] The waiter of A cannot open owner-only pages.
- [ ] In the browser, signed in as Owner A, a request to read B's rows (for example `orders?restaurant_id=eq.<B id>` through the REST API) returns an empty list.

**Subscriptions**
- [ ] An expired restaurant is blocked as designed and shows the expiry message.
- [ ] Renewing extends from the later of today and the current expiry.

**Edge Functions**
- [ ] Creating a restaurant and an employee works from the dashboard.
- [ ] A Waiter cannot call the `admin-users` function successfully (it replies 403).
- [ ] AI test button reports "Connection successful" (only if you set up AI).
- [ ] With AI off or no key, the customer page still shows a basic "Ideas" suggestion.

---

## 5. What was actually tested, and what was not

I ran these in a development sandbox:
- `node --test tests/customer_core.test.js`: 11 customer-logic tests, **passed**.
- `node --test tests/edge_functions.test.js`: 17 tests of the Edge Function logic using fake database and Gemini clients, **passed**.
- `tests/test_backend.py`: 21 passed, 1 skipped (run with a small stand-in runner because `pytest` could not be installed there).
- `database.sql` loaded on a local Postgres 16 with small stand-ins for Supabase's `auth` and `storage` schemas, and `tests/test_database.py` passed 333 of 333 checks (tenant isolation, role limits, order rules, refunds, subscriptions, table-wise waiter assignment).
- The customer page was exercised in headless Chromium against the real SQL functions through a small local shim.

**Not tested** (you must verify these yourself with the checklist above):
- Anything on a real Supabase project: real Auth sign-in and password-reset email, Storage uploads and policies, and PostgREST role handling.
- The Edge Functions running under Deno on Supabase (tests ran their logic on Node with fakes).
- Real Gemini calls.
- The deployed Streamlit dashboard in a browser, on phones, and under real load.
- Printing QR codes and scanning them in real restaurants.
- A professional security review. Do one before charging customers.

---

## 6. Honest feature status

**Built**
- Table-wise waiters: each table belongs to one waiter (a waiter has many tables). Orders and requests go to that table's waiter, who is the only waiter who sees them and who earns the commission. The Owner can reassign a table, or move all of a waiter's tables when they are on leave. The Owner does not assign individual orders.
- Three roles with database-enforced permissions (Row Level Security, column-level grants, role checks inside functions).
- QR table ordering with server-calculated prices and totals, duplicate-submission protection, and per-table throttling.
- Order status workflow, waiter requests, menu, tables, expenses, staff, commissions, refunds, and revenue reports (Pakistan time).
- Profit shown only when every sold item has a cost price. Revenue and profit are kept separate.
- Subscriptions: plans, payments, renewals, expiry gate, messages from Super Admin.
- Optional Gemini suggestions through an Edge Function. The model can only pick real dish ids, and prices are recomputed from the database.

**Not built or planned**
- **Inventory is not deducted automatically when orders are completed.** Stock is adjusted only by manual entries.
- Partial refunds (only full refunds of completed orders).
- Live push updates. The dashboard polls about every 10 seconds; the customer page about every 8 seconds.
- Online payments (payment proofs for subscriptions are uploaded manually).
- Urdu or other languages (labels are customizable, but there is no translation pack).
- Final subscription prices. Paid plans are seeded at Rs. 0 and the 14-day trial is a placeholder. Set your own in the Super Admin screens.
- AI rate limiting is best-effort (it resets if the function restarts). Keep AI off if you need hard limits.

---

## 7. Security notes

- Never commit `.streamlit/secrets.toml`. `.gitignore` blocks it.
- The `service_role` key is never used in the dashboard, customer page or this repository. If you ever pasted it somewhere public, rotate it in Supabase (Project Settings → API).
- The Gemini key exists only as an Edge Function secret.
- Table QR tokens are secret 64-character values. If a QR code leaks, regenerate that table's token in the dashboard.
- The customer page renders text with `textContent` only and ships a Content-Security-Policy.
- `tests/supabase_stubs.sql` is for throwaway test databases only. Never run it on your real project.

---

## 8. Running the tests yourself

```
node --test tests/customer_core.test.js
node --test tests/edge_functions.test.js     # needs Node 22.6 or newer
pip install pytest && pytest tests/test_backend.py
```
Database tests (a throwaway local Postgres 16 on Linux only, never your Supabase project). The setup commands are at the top of `tests/test_database.py`; the last line it prints is `333/333 checks passed` when everything works.
Run `node --test` with the file path, not with the folder name.

---

## 9. Troubleshooting

| Problem | Likely cause and fix |
|---|---|
| Dashboard shows a setup screen | Streamlit secrets are missing or wrong. Re-check `SUPABASE_URL` and `SUPABASE_ANON_KEY`. |
| "Refusing to use a service-role key" | You pasted the wrong key. Use the anon / publishable key. |
| Reset-password link opens the login page | The email template link is not the one from Step 3, or Site URL is wrong. |
| Creating a restaurant fails with 401 | The `admin-users` function is not deployed, or you are not signed in as Super Admin. |
| Creating a restaurant fails with a gateway or JWT message | Turn "JWT verification" off for the function (Step 5). |
| Customer page says "invalid QR" | `config.js` still has placeholders, the QR was made before `CUSTOMER_APP_URL` was set (download the QR again), or the token was regenerated. |
| Customer page blank, console shows blocked requests | `_headers` CSP allows only `*.supabase.co`. If you use a custom Supabase domain, add it to `connect-src`. |
| AI suggestions never appear | AI switched off, no `GEMINI_API_KEY` secret, or `ENABLE_AI` is false. The basic suggestion still works. |
| Emails are slow or missing | Built-in Supabase email is rate-limited. Add SMTP. |
