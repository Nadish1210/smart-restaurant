-- =============================================================================
-- SMART RESTAURANT - database.sql
-- AI-Powered QR Ordering & Intelligent Restaurant Management System
--
-- HOW TO RUN (beginner): Supabase dashboard -> SQL Editor -> New query ->
-- paste this ENTIRE file -> Run. It is safe to run again later (it will not
-- delete your data). Then follow the README to create the first Super Admin.
--
-- SECURITY DESIGN (short version)
--   * Every table has Row Level Security (RLS). A signed-in user can only see
--     rows of their own restaurant. The Super Admin has platform-wide access.
--   * Users can NEVER write privileged columns (role, status, subscription,
--     prices, order totals). Column-level privileges block it, and the only
--     way to change those is through the checked functions below.
--   * Customers (anonymous, via QR code) cannot read or write any table.
--     They can only call the customer_* functions, which look the table up by
--     its secret token, read prices from the database and compute totals.
--   * Functions that run with elevated rights (SECURITY DEFINER) set an empty
--     search_path, use fully qualified names, and check the caller first.
--   * Functions that create Auth users run only through the `admin-users`
--     Edge Function with the service-role key (never in the browser).
--
-- SECTIONS
--   1 Tables            2 Helper functions    3 Triggers
--   4 RLS policies      5 Staff functions     6 Customer (QR) functions
--   7 Super Admin functions   8 Service functions (Edge Function only)
--   9 Storage buckets   10 Grants   11 Seed data
-- =============================================================================

-- -----------------------------------------------------------------------------
-- 1. TABLES
-- -----------------------------------------------------------------------------

create table if not exists public.restaurants (
  id                          uuid primary key default gen_random_uuid(),
  name                        text not null check (char_length(btrim(name)) between 1 and 100),
  phone                       text check (phone is null or char_length(phone) <= 30),
  email                       text check (email is null or char_length(email) <= 254),
  address                     text check (address is null or char_length(address) <= 200),
  city                        text check (city is null or char_length(city) <= 60),
  logo_url                    text check (logo_url is null or logo_url ~ '^https://'),
  status                      text not null default 'active' check (status in ('active', 'suspended')),
  owner_id                    uuid,                       -- FK added after profiles exists
  commission_basis            text not null default 'gross' check (commission_basis in ('gross', 'net')),
  escalation_minutes          integer not null default 5 check (escalation_minutes between 1 and 120),
  waiter_can_view_sales       boolean not null default true,
  waiter_can_view_commission  boolean not null default true,
  next_order_number           bigint not null default 1,  -- per-restaurant order counter
  created_at                  timestamptz not null default now(),
  updated_at                  timestamptz not null default now()
);

create table if not exists public.profiles (
  id             uuid primary key references auth.users (id) on delete cascade,
  email          text not null,
  full_name      text,
  role           text not null check (role in ('super_admin', 'owner', 'waiter')),
  restaurant_id  uuid references public.restaurants (id) on delete cascade,
  is_active      boolean not null default true,
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now(),
  constraint profiles_role_scope check (
    (role = 'super_admin' and restaurant_id is null) or
    (role in ('owner', 'waiter') and restaurant_id is not null)
  )
);
create unique index if not exists profiles_email_key on public.profiles (lower(email));
create unique index if not exists profiles_one_active_owner on public.profiles (restaurant_id)
  where role = 'owner' and is_active;
create index if not exists profiles_restaurant_idx on public.profiles (restaurant_id);

do $$
begin
  if not exists (select 1 from pg_constraint where conname = 'restaurants_owner_fk') then
    alter table public.restaurants
      add constraint restaurants_owner_fk foreign key (owner_id) references public.profiles (id) on delete set null;
  end if;
end $$;

create table if not exists public.restaurant_employees (
  id              uuid primary key default gen_random_uuid(),
  restaurant_id   uuid not null references public.restaurants (id) on delete cascade,
  user_id         uuid not null unique references public.profiles (id) on delete cascade,
  full_name       text not null check (char_length(btrim(full_name)) between 1 and 100),
  email           text not null,
  phone           text check (phone is null or char_length(phone) <= 30),
  commission_pct  numeric(5,2) not null default 0 check (commission_pct between 0 and 100),
  is_active       boolean not null default true,
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now()
);
create index if not exists employees_restaurant_idx on public.restaurant_employees (restaurant_id);

create table if not exists public.subscription_plans (
  id             uuid primary key default gen_random_uuid(),
  code           text not null unique check (code ~ '^[a-z0-9_]{2,40}$'),
  name           text not null check (char_length(btrim(name)) between 1 and 60),
  price_pkr      numeric(12,2) not null default 0 check (price_pkr >= 0),
  duration_days  integer not null default 30 check (duration_days between 1 and 3660),
  is_active      boolean not null default true,
  description    text not null default '' check (char_length(description) <= 500),
  sort_order     integer not null default 0,
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now()
);

create table if not exists public.subscriptions (
  id             uuid primary key default gen_random_uuid(),
  restaurant_id  uuid not null unique references public.restaurants (id) on delete cascade,
  plan_code      text not null references public.subscription_plans (code) on update cascade,
  status         text not null check (status in ('trial', 'active', 'expired', 'suspended')),
  start_date     date not null,
  expiry_date    date not null,
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now(),
  constraint subscriptions_dates check (expiry_date >= start_date)
);

create table if not exists public.subscription_payments (
  id                uuid primary key default gen_random_uuid(),
  restaurant_id     uuid not null references public.restaurants (id) on delete cascade,
  plan_code         text not null references public.subscription_plans (code) on update cascade,
  amount            numeric(12,2) not null check (amount > 0),
  reference         text check (reference is null or char_length(reference) <= 100),
  note              text check (note is null or char_length(note) <= 500),
  proof_path        text,
  status            text not null default 'pending' check (status in ('pending', 'approved', 'rejected')),
  rejection_reason  text check (rejection_reason is null or char_length(rejection_reason) <= 500),
  review_note       text check (review_note is null or char_length(review_note) <= 500),
  reviewed_by       uuid references public.profiles (id) on delete set null,
  reviewed_at       timestamptz,
  created_at        timestamptz not null default now(),
  constraint payments_rejection_needs_reason check (
    status <> 'rejected' or char_length(btrim(coalesce(rejection_reason, ''))) > 0
  )
);
create index if not exists payments_restaurant_idx on public.subscription_payments (restaurant_id, created_at desc);
create index if not exists payments_status_idx on public.subscription_payments (status, created_at desc);

create table if not exists public.restaurant_messages (
  id             uuid primary key default gen_random_uuid(),
  restaurant_id  uuid not null references public.restaurants (id) on delete cascade,
  title          text not null check (char_length(btrim(title)) between 1 and 120),
  body           text not null check (char_length(btrim(body)) between 1 and 2000),
  kind           text not null default 'admin' check (kind in ('admin', 'expiry_reminder', 'subscription', 'payment')),
  is_read        boolean not null default false,
  created_by     uuid references public.profiles (id) on delete set null,
  created_at     timestamptz not null default now()
);
create index if not exists messages_restaurant_idx on public.restaurant_messages (restaurant_id, created_at desc);

create table if not exists public.menu_categories (
  id             uuid primary key default gen_random_uuid(),
  restaurant_id  uuid not null references public.restaurants (id) on delete cascade,
  name           text not null check (char_length(btrim(name)) between 1 and 60),
  sort_order     integer not null default 0 check (sort_order between 0 and 10000),
  is_active      boolean not null default true,
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now(),
  unique (id, restaurant_id)
);
create unique index if not exists categories_name_key on public.menu_categories (restaurant_id, lower(name));

create table if not exists public.menu_items (
  id             uuid primary key default gen_random_uuid(),
  restaurant_id  uuid not null references public.restaurants (id) on delete cascade,
  category_id    uuid not null,
  name           text not null check (char_length(btrim(name)) between 1 and 100),
  description    text check (description is null or char_length(description) <= 500),
  price          numeric(12,2) not null check (price > 0),
  cost_price     numeric(12,2) check (cost_price is null or cost_price >= 0),
  image_url      text check (image_url is null or image_url ~ '^https://'),
  is_available   boolean not null default true,   -- temporarily out of stock
  is_active      boolean not null default true,   -- false = deactivated (hidden)
  is_featured    boolean not null default false,
  is_popular     boolean not null default false,
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now(),
  constraint menu_items_category_fk foreign key (category_id, restaurant_id)
    references public.menu_categories (id, restaurant_id)
);
create index if not exists menu_items_restaurant_idx on public.menu_items (restaurant_id, category_id);

create table if not exists public.restaurant_tables (
  id             uuid primary key default gen_random_uuid(),
  restaurant_id  uuid not null references public.restaurants (id) on delete cascade,
  label          text not null check (char_length(btrim(label)) between 1 and 30),
  seats          integer not null default 4 check (seats between 1 and 50),
  -- Secret, unguessable QR token (two random UUIDs = 64 hex characters).
  token          text not null unique
                   default replace(gen_random_uuid()::text || gen_random_uuid()::text, '-', '')
                   check (token ~ '^[A-Za-z0-9_-]{16,128}$'),
  is_active      boolean not null default true,
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now()
);
create unique index if not exists tables_label_key on public.restaurant_tables (restaurant_id, lower(label));

create table if not exists public.orders (
  id               uuid primary key default gen_random_uuid(),
  restaurant_id    uuid not null references public.restaurants (id) on delete cascade,
  order_number     bigint not null,
  table_id         uuid references public.restaurant_tables (id) on delete set null,
  table_label      text not null,
  waiter_id        uuid references public.restaurant_employees (id) on delete set null,
  waiter_name      text,
  status           text not null default 'received' check (status in
                     ('received', 'confirmed', 'preparing', 'ready', 'served', 'completed', 'cancelled')),
  subtotal         numeric(12,2) not null check (subtotal >= 0),
  discount         numeric(12,2) not null default 0 check (discount >= 0),
  total            numeric(12,2) not null check (total >= 0),
  notes            text check (notes is null or char_length(notes) <= 500),
  idempotency_key  text check (idempotency_key is null or char_length(idempotency_key) between 8 and 80),
  cancel_reason    text,
  cancelled_at     timestamptz,
  completed_at     timestamptz,
  refunded_at      timestamptz,
  refund_reason    text,
  created_at       timestamptz not null default now(),
  updated_at       timestamptz not null default now(),
  unique (restaurant_id, order_number),
  constraint orders_total_math check (discount <= subtotal and total = subtotal - discount),
  constraint orders_cancel_needs_time check (status <> 'cancelled' or cancelled_at is not null),
  constraint orders_refund_only_completed check (refunded_at is null or status = 'completed')
);
create index if not exists orders_restaurant_created_idx on public.orders (restaurant_id, created_at desc);
create index if not exists orders_restaurant_status_idx on public.orders (restaurant_id, status);
create index if not exists orders_table_created_idx on public.orders (table_id, created_at desc);
create index if not exists orders_completed_idx on public.orders (restaurant_id, completed_at) where status = 'completed';
create index if not exists orders_waiter_idx on public.orders (waiter_id);
create unique index if not exists orders_idempotency_key on public.orders (restaurant_id, idempotency_key)
  where idempotency_key is not null;

create table if not exists public.order_items (
  id             uuid primary key default gen_random_uuid(),
  order_id       uuid not null references public.orders (id) on delete cascade,
  restaurant_id  uuid not null references public.restaurants (id) on delete cascade,
  menu_item_id   uuid references public.menu_items (id) on delete set null,
  name           text not null,                              -- snapshot at order time
  unit_price     numeric(12,2) not null check (unit_price >= 0),
  unit_cost      numeric(12,2) check (unit_cost is null or unit_cost >= 0),
  quantity       integer not null check (quantity between 1 and 50),
  line_total     numeric(12,2) not null check (line_total >= 0),
  note           text check (note is null or char_length(note) <= 200),
  constraint order_items_math check (line_total = unit_price * quantity)
);
create index if not exists order_items_order_idx on public.order_items (order_id);
create index if not exists order_items_restaurant_idx on public.order_items (restaurant_id, menu_item_id);

create table if not exists public.waiter_requests (
  id             uuid primary key default gen_random_uuid(),
  restaurant_id  uuid not null references public.restaurants (id) on delete cascade,
  table_id       uuid references public.restaurant_tables (id) on delete set null,
  table_label    text not null,
  request_type   text not null check (request_type in
                   ('call_waiter', 'bill', 'water', 'cutlery', 'assistance', 'other')),
  note           text check (note is null or char_length(note) <= 200),
  status         text not null default 'pending' check (status in
                   ('pending', 'accepted', 'in_progress', 'completed', 'cancelled')),
  escalated      boolean not null default false,
  escalated_at   timestamptz,
  accepted_by    uuid references public.profiles (id) on delete set null,
  accepted_at    timestamptz,
  completed_at   timestamptz,
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now()
);
create index if not exists requests_restaurant_idx on public.waiter_requests (restaurant_id, status, created_at desc);
-- Prevent duplicate alerts: one open request of each type per table.
create unique index if not exists requests_one_open_per_type on public.waiter_requests (table_id, request_type)
  where status in ('pending', 'accepted', 'in_progress') and table_id is not null;

-- Waiters see their own sales and commission by default (the owner can switch this off per restaurant).
alter table public.restaurants alter column waiter_can_view_sales set default true;
alter table public.restaurants alter column waiter_can_view_commission set default true;

-- Each table belongs to at most one waiter; a waiter may have many tables. Orders and
-- requests from a table are credited to / handled by that table's waiter.
alter table public.restaurant_tables
  add column if not exists waiter_id uuid references public.restaurant_employees (id) on delete set null;
create index if not exists tables_waiter_idx on public.restaurant_tables (waiter_id);
alter table public.waiter_requests
  add column if not exists waiter_id uuid references public.restaurant_employees (id) on delete set null;
create index if not exists requests_waiter_idx on public.waiter_requests (waiter_id);

create table if not exists public.inventory (
  id                 uuid primary key default gen_random_uuid(),
  restaurant_id      uuid not null references public.restaurants (id) on delete cascade,
  name               text not null check (char_length(btrim(name)) between 1 and 80),
  unit               text not null check (char_length(btrim(unit)) between 1 and 20),
  quantity           numeric(14,2) not null default 0 check (quantity >= 0),
  reorder_threshold  numeric(14,2) not null default 0 check (reorder_threshold >= 0),
  cost_per_unit      numeric(12,2) not null default 0 check (cost_per_unit >= 0),
  created_at         timestamptz not null default now(),
  updated_at         timestamptz not null default now()
);
create unique index if not exists inventory_name_key on public.inventory (restaurant_id, lower(name));

create table if not exists public.inventory_transactions (
  id             uuid primary key default gen_random_uuid(),
  restaurant_id  uuid not null references public.restaurants (id) on delete cascade,
  inventory_id   uuid not null references public.inventory (id) on delete cascade,
  kind           text not null check (kind in ('addition', 'deduction')),
  quantity       numeric(14,2) not null check (quantity > 0),
  balance_after  numeric(14,2) not null check (balance_after >= 0),
  reason         text check (reason is null or char_length(reason) <= 200),
  created_by     uuid,
  created_at     timestamptz not null default now()
);
create index if not exists inv_tx_item_idx on public.inventory_transactions (inventory_id, created_at desc);
create index if not exists inv_tx_restaurant_idx on public.inventory_transactions (restaurant_id, created_at desc);

create table if not exists public.expenses (
  id             uuid primary key default gen_random_uuid(),
  restaurant_id  uuid not null references public.restaurants (id) on delete cascade,
  expense_date   date not null,
  category       text not null check (char_length(btrim(category)) between 1 and 50),
  description    text check (description is null or char_length(description) <= 200),
  amount         numeric(12,2) not null check (amount > 0),
  created_by     uuid default auth.uid(),
  created_at     timestamptz not null default now()
);
create index if not exists expenses_restaurant_date_idx on public.expenses (restaurant_id, expense_date);

-- One row per COMPLETED order. The unique order_id is what prevents the same
-- order from ever earning commission twice. A refund flips status to 'reversed'.
create table if not exists public.sales_records (
  id                uuid primary key default gen_random_uuid(),
  restaurant_id     uuid not null references public.restaurants (id) on delete cascade,
  order_id          uuid not null unique references public.orders (id) on delete cascade,
  waiter_id         uuid references public.restaurant_employees (id) on delete set null,
  completed_at      timestamptz not null,
  gross_amount      numeric(12,2) not null,
  net_amount        numeric(12,2) not null,
  commission_basis  text not null check (commission_basis in ('gross', 'net')),
  base_amount       numeric(12,2) not null,
  commission_pct    numeric(5,2) not null default 0,
  commission_amount numeric(12,2) not null default 0,
  status            text not null default 'earned' check (status in ('earned', 'reversed')),
  reversed_at       timestamptz,
  created_at        timestamptz not null default now()
);
create index if not exists sales_restaurant_idx on public.sales_records (restaurant_id, completed_at);
create index if not exists sales_waiter_idx on public.sales_records (waiter_id, completed_at);

create table if not exists public.system_settings (
  key         text primary key check (key ~ '^[a-z_]{2,60}$'),
  value       jsonb not null,
  updated_at  timestamptz not null default now(),
  updated_by  uuid
);

create table if not exists public.audit_logs (
  id             uuid primary key default gen_random_uuid(),
  actor_id       uuid,
  actor_email    text,
  restaurant_id  uuid,
  action         text not null,
  entity         text,
  entity_id      uuid,
  details        jsonb not null default '{}'::jsonb,
  created_at     timestamptz not null default now()
);
create index if not exists audit_created_idx on public.audit_logs (created_at desc);
create index if not exists audit_restaurant_idx on public.audit_logs (restaurant_id, created_at desc);

-- -----------------------------------------------------------------------------
-- 2. HELPER FUNCTIONS (used by policies and by the functions further down)
--    All use an empty search_path and fully qualified names (safe).
-- -----------------------------------------------------------------------------

create or replace function public.pkt_today() returns date
language sql stable set search_path = ''
as $$ select (now() at time zone 'Asia/Karachi')::date $$;

-- Start of a calendar day in Pakistan time, as an absolute timestamp.
create or replace function public.pkt_start(d date) returns timestamptz
language sql stable set search_path = ''
as $$ select d::timestamp at time zone 'Asia/Karachi' $$;

create or replace function public.auth_role() returns text
language sql stable security definer set search_path = ''
as $$
  select p.role from public.profiles p where p.id = (select auth.uid()) and p.is_active
$$;

create or replace function public.auth_restaurant_id() returns uuid
language sql stable security definer set search_path = ''
as $$
  select p.restaurant_id from public.profiles p where p.id = (select auth.uid()) and p.is_active
$$;

-- The caller's employee id when the caller is an active waiter (null for everyone else).
create or replace function public.auth_employee_id() returns uuid
language sql stable security definer set search_path = ''
as $$
  select e.id from public.restaurant_employees e
  join public.profiles p on p.id = e.user_id
  where e.user_id = (select auth.uid()) and p.is_active and p.role = 'waiter' and e.is_active
$$;

create or replace function public.is_super_admin() returns boolean
language sql stable security definer set search_path = ''
as $$
  select exists (select 1 from public.profiles p
                 where p.id = (select auth.uid()) and p.is_active and p.role = 'super_admin')
$$;

create or replace function public.is_owner_of(rid uuid) returns boolean
language sql stable security definer set search_path = ''
as $$
  select exists (select 1 from public.profiles p
                 where p.id = (select auth.uid()) and p.is_active
                   and p.role = 'owner' and p.restaurant_id = rid)
$$;

create or replace function public.is_member_of(rid uuid) returns boolean
language sql stable security definer set search_path = ''
as $$
  select exists (select 1 from public.profiles p
                 where p.id = (select auth.uid()) and p.is_active
                   and p.role in ('owner', 'waiter') and p.restaurant_id = rid)
$$;

-- True when the restaurant is active AND has an unexpired trial/active subscription.
create or replace function public.restaurant_is_open(rid uuid) returns boolean
language sql stable security definer set search_path = ''
as $$
  select exists (
    select 1
    from public.restaurants r
    join public.subscriptions s on s.restaurant_id = r.id
    where r.id = rid and r.status = 'active'
      and s.status in ('trial', 'active')
      and s.expiry_date >= public.pkt_today())
$$;

-- Internal guards. Not callable by clients (see grants).
create or replace function public._require_super() returns void
language plpgsql stable security definer set search_path = ''
as $$
begin
  if not public.is_super_admin() then
    raise exception 'permission denied' using errcode = '42501';
  end if;
end $$;

-- Returns the caller's restaurant id if the caller has one of the roles.
create or replace function public._staff_restaurant(p_roles text[]) returns uuid
language plpgsql stable security definer set search_path = ''
as $$
declare v_role text; v_rid uuid;
begin
  select p.role, p.restaurant_id into v_role, v_rid
  from public.profiles p where p.id = auth.uid() and p.is_active;
  if v_role is null or v_rid is null or not (v_role = any (p_roles)) then
    raise exception 'permission denied' using errcode = '42501';
  end if;
  return v_rid;
end $$;

create or replace function public._require_open(p_rid uuid) returns void
language plpgsql stable security definer set search_path = ''
as $$
begin
  if not public.restaurant_is_open(p_rid) then
    raise exception 'Your subscription is not active. Please renew it to continue.';
  end if;
end $$;

create or replace function public.log_audit(
  p_action text, p_entity text, p_entity_id uuid, p_restaurant_id uuid,
  p_details jsonb default '{}'::jsonb, p_actor uuid default null
) returns void
language plpgsql security definer set search_path = ''
as $$
declare v_actor uuid := coalesce(p_actor, auth.uid());
begin
  insert into public.audit_logs (actor_id, actor_email, restaurant_id, action, entity, entity_id, details)
  values (v_actor, (select p.email from public.profiles p where p.id = v_actor),
          p_restaurant_id, p_action, p_entity, p_entity_id, coalesce(p_details, '{}'::jsonb));
end $$;

-- -----------------------------------------------------------------------------
-- 3. TRIGGERS
-- -----------------------------------------------------------------------------

create or replace function public.set_updated_at() returns trigger
language plpgsql set search_path = ''
as $$
begin
  new.updated_at := now();
  return new;
end $$;

do $$
declare t text;
begin
  foreach t in array array['restaurants', 'profiles', 'restaurant_employees', 'subscription_plans',
    'subscriptions', 'menu_categories', 'menu_items', 'restaurant_tables', 'orders',
    'waiter_requests', 'inventory']
  loop
    execute format('drop trigger if exists trg_%1$s_updated on public.%1$I', t);
    execute format('create trigger trg_%1$s_updated before update on public.%1$I
                    for each row execute function public.set_updated_at()', t);
  end loop;
end $$;

-- Keep the login profile in step with the employee record (deactivating a
-- waiter also blocks their login).
create or replace function public.sync_employee_profile() returns trigger
language plpgsql security definer set search_path = ''
as $$
declare v_id uuid;
begin
  update public.profiles set is_active = new.is_active, full_name = new.full_name
  where id = new.user_id;
  -- A deactivated waiter keeps no tables; the owner sees them as "no waiter" and assigns them again.
  if not new.is_active then
    for v_id in select t.id from public.restaurant_tables t where t.waiter_id = new.id loop
      perform public._move_table(new.restaurant_id, v_id, null);
    end loop;
  end if;
  return new;
end $$;

drop trigger if exists trg_employee_sync on public.restaurant_employees;
create trigger trg_employee_sync
  after update of is_active, full_name on public.restaurant_employees
  for each row execute function public.sync_employee_profile();

-- -----------------------------------------------------------------------------
-- 4. ROW LEVEL SECURITY
--    Reads: members see their own restaurant only. Writes that clients are
--    allowed to do directly are limited to the owner of an OPEN restaurant.
--    Everything else goes through the functions in sections 5-8.
-- -----------------------------------------------------------------------------

do $$
declare t text;
begin
  foreach t in array array['restaurants', 'profiles', 'restaurant_employees', 'subscription_plans',
    'subscriptions', 'subscription_payments', 'restaurant_messages', 'menu_categories', 'menu_items',
    'restaurant_tables', 'orders', 'order_items', 'waiter_requests', 'inventory',
    'inventory_transactions', 'expenses', 'sales_records', 'system_settings', 'audit_logs']
  loop
    execute format('alter table public.%I enable row level security', t);
  end loop;
end $$;

-- profiles: you can read yourself; the Super Admin reads all. Nobody writes
-- through the API, so nobody can give themselves a role.
drop policy if exists profiles_select on public.profiles;
create policy profiles_select on public.profiles for select to authenticated
  using (id = (select auth.uid()) or (select public.is_super_admin()));

-- restaurants
drop policy if exists restaurants_select on public.restaurants;
create policy restaurants_select on public.restaurants for select to authenticated
  using ((select public.is_super_admin()) or (select public.is_member_of(id)));
drop policy if exists restaurants_update on public.restaurants;
create policy restaurants_update on public.restaurants for update to authenticated
  using ((select public.is_owner_of(id)) and (select public.restaurant_is_open(id)))
  with check ((select public.is_owner_of(id)) and (select public.restaurant_is_open(id)));

-- employees: owner sees all of their staff; a waiter sees only themselves.
drop policy if exists employees_select on public.restaurant_employees;
create policy employees_select on public.restaurant_employees for select to authenticated
  using ((select public.is_super_admin()) or (select public.is_owner_of(restaurant_id))
         or user_id = (select auth.uid()));
drop policy if exists employees_update on public.restaurant_employees;
create policy employees_update on public.restaurant_employees for update to authenticated
  using ((select public.is_owner_of(restaurant_id)) and (select public.restaurant_is_open(restaurant_id)))
  with check ((select public.is_owner_of(restaurant_id)) and (select public.restaurant_is_open(restaurant_id)));

-- plans: everyone signed in sees active plans; the Super Admin sees all.
drop policy if exists plans_select on public.subscription_plans;
create policy plans_select on public.subscription_plans for select to authenticated
  using (is_active or (select public.is_super_admin()));

-- subscriptions / payments / messages: owner of that restaurant or Super Admin.
-- (Waiters cannot see them. Nobody writes directly: see functions.)
drop policy if exists subscriptions_select on public.subscriptions;
create policy subscriptions_select on public.subscriptions for select to authenticated
  using ((select public.is_super_admin()) or (select public.is_owner_of(restaurant_id)));
drop policy if exists payments_select on public.subscription_payments;
create policy payments_select on public.subscription_payments for select to authenticated
  using ((select public.is_super_admin()) or (select public.is_owner_of(restaurant_id)));
drop policy if exists messages_select on public.restaurant_messages;
create policy messages_select on public.restaurant_messages for select to authenticated
  using ((select public.is_super_admin()) or (select public.is_owner_of(restaurant_id)));

-- menu, tables: all staff of the restaurant read; the owner writes.
drop policy if exists categories_select on public.menu_categories;
create policy categories_select on public.menu_categories for select to authenticated
  using ((select public.is_member_of(restaurant_id)));
drop policy if exists categories_insert on public.menu_categories;
create policy categories_insert on public.menu_categories for insert to authenticated
  with check ((select public.is_owner_of(restaurant_id)) and (select public.restaurant_is_open(restaurant_id)));
drop policy if exists categories_update on public.menu_categories;
create policy categories_update on public.menu_categories for update to authenticated
  using ((select public.is_owner_of(restaurant_id)) and (select public.restaurant_is_open(restaurant_id)))
  with check ((select public.is_owner_of(restaurant_id)) and (select public.restaurant_is_open(restaurant_id)));

drop policy if exists items_select on public.menu_items;
create policy items_select on public.menu_items for select to authenticated
  using ((select public.is_member_of(restaurant_id)));
drop policy if exists items_insert on public.menu_items;
create policy items_insert on public.menu_items for insert to authenticated
  with check ((select public.is_owner_of(restaurant_id)) and (select public.restaurant_is_open(restaurant_id)));
drop policy if exists items_update on public.menu_items;
create policy items_update on public.menu_items for update to authenticated
  using ((select public.is_owner_of(restaurant_id)) and (select public.restaurant_is_open(restaurant_id)))
  with check ((select public.is_owner_of(restaurant_id)) and (select public.restaurant_is_open(restaurant_id)));

drop policy if exists rtables_select on public.restaurant_tables;
create policy rtables_select on public.restaurant_tables for select to authenticated
  using ((select public.is_member_of(restaurant_id)));
drop policy if exists rtables_insert on public.restaurant_tables;
create policy rtables_insert on public.restaurant_tables for insert to authenticated
  with check ((select public.is_owner_of(restaurant_id)) and (select public.restaurant_is_open(restaurant_id)));
drop policy if exists rtables_update on public.restaurant_tables;
create policy rtables_update on public.restaurant_tables for update to authenticated
  using ((select public.is_owner_of(restaurant_id)) and (select public.restaurant_is_open(restaurant_id)))
  with check ((select public.is_owner_of(restaurant_id)) and (select public.restaurant_is_open(restaurant_id)));

-- orders, items, requests: the owner reads all; a waiter reads only the orders and requests of
-- the tables assigned to them. Changes only via functions.
drop policy if exists orders_select on public.orders;
create policy orders_select on public.orders for select to authenticated
  using ((select public.is_owner_of(restaurant_id))
         or ((select public.is_member_of(restaurant_id)) and waiter_id = (select public.auth_employee_id())));
drop policy if exists order_items_select on public.order_items;
create policy order_items_select on public.order_items for select to authenticated
  using ((select public.is_owner_of(restaurant_id))
         or exists (select 1 from public.orders o where o.id = order_id));  -- orders RLS decides
drop policy if exists requests_select on public.waiter_requests;
create policy requests_select on public.waiter_requests for select to authenticated
  using ((select public.is_owner_of(restaurant_id))
         or ((select public.is_member_of(restaurant_id)) and waiter_id = (select public.auth_employee_id())));

-- financial records: owner only (waiters get their own figures from waiter_stats()).
drop policy if exists inventory_select on public.inventory;
create policy inventory_select on public.inventory for select to authenticated
  using ((select public.is_owner_of(restaurant_id)));
drop policy if exists inventory_insert on public.inventory;
create policy inventory_insert on public.inventory for insert to authenticated
  with check ((select public.is_owner_of(restaurant_id)) and (select public.restaurant_is_open(restaurant_id))
              and quantity = 0);  -- stock only changes through adjust_stock(), which records a movement
drop policy if exists inventory_update on public.inventory;
create policy inventory_update on public.inventory for update to authenticated
  using ((select public.is_owner_of(restaurant_id)) and (select public.restaurant_is_open(restaurant_id)))
  with check ((select public.is_owner_of(restaurant_id)) and (select public.restaurant_is_open(restaurant_id)));
drop policy if exists inv_tx_select on public.inventory_transactions;
create policy inv_tx_select on public.inventory_transactions for select to authenticated
  using ((select public.is_owner_of(restaurant_id)));

drop policy if exists expenses_select on public.expenses;
create policy expenses_select on public.expenses for select to authenticated
  using ((select public.is_owner_of(restaurant_id)));
drop policy if exists expenses_insert on public.expenses;
create policy expenses_insert on public.expenses for insert to authenticated
  with check ((select public.is_owner_of(restaurant_id)) and (select public.restaurant_is_open(restaurant_id)));
drop policy if exists expenses_delete on public.expenses;
create policy expenses_delete on public.expenses for delete to authenticated
  using ((select public.is_owner_of(restaurant_id)) and (select public.restaurant_is_open(restaurant_id)));

drop policy if exists sales_select on public.sales_records;
create policy sales_select on public.sales_records for select to authenticated
  using ((select public.is_owner_of(restaurant_id)));

-- platform tables: Super Admin only.
drop policy if exists settings_select on public.system_settings;
create policy settings_select on public.system_settings for select to authenticated
  using ((select public.is_super_admin()));
drop policy if exists audit_select on public.audit_logs;
create policy audit_select on public.audit_logs for select to authenticated
  using ((select public.is_super_admin()));

-- -----------------------------------------------------------------------------
-- 5. STAFF FUNCTIONS (owner / waiter, called by the Streamlit dashboard)
-- -----------------------------------------------------------------------------

-- Who am I? Used once at sign-in.
create or replace function public.get_my_context() returns jsonb
language plpgsql stable security definer set search_path = ''
as $$
declare v record;
begin
  select p.id as user_id, p.email, p.full_name, p.role, p.restaurant_id, p.is_active,
         r.name as restaurant_name, e.id as employee_id
    into v
  from public.profiles p
  left join public.restaurants r on r.id = p.restaurant_id
  left join public.restaurant_employees e on e.user_id = p.id
  where p.id = auth.uid();
  if not found then
    return '{}'::jsonb;
  end if;
  return jsonb_build_object('user_id', v.user_id, 'email', v.email, 'full_name', v.full_name,
    'role', v.role, 'restaurant_id', v.restaurant_id, 'restaurant_name', v.restaurant_name,
    'employee_id', v.employee_id, 'is_active', v.is_active);
end $$;

-- May this user's restaurant use the app right now? (Fails closed.)
create or replace function public.get_access_gate() returns jsonb
language plpgsql stable security definer set search_path = ''
as $$
declare v_rid uuid; v record; v_days integer; v_reason text; v_allowed boolean := false; v_status text;
begin
  v_rid := public.auth_restaurant_id();
  if v_rid is null then
    return jsonb_build_object('allowed', false, 'reason', 'No restaurant is linked to this account.');
  end if;
  select r.status as rstatus, s.status as sstatus, s.expiry_date, p.name as plan_name
    into v
  from public.restaurants r
  left join public.subscriptions s on s.restaurant_id = r.id
  left join public.subscription_plans p on p.code = s.plan_code
  where r.id = v_rid;
  v_days := case when v.expiry_date is null then null else v.expiry_date - public.pkt_today() end;
  v_status := v.sstatus;
  if v.rstatus is distinct from 'active' then
    v_reason := 'This restaurant account is suspended. Please contact the platform administrator.';
  elsif v.sstatus is null then
    v_reason := 'No subscription was found for this restaurant. Please contact the platform administrator.';
  elsif v.sstatus = 'suspended' then
    v_reason := 'The subscription is suspended. Please contact the platform administrator.';
  elsif v.sstatus = 'expired' or coalesce(v_days, -1) < 0 then
    v_status := 'expired';
    v_reason := 'The subscription has expired. The owner can renew it from the Subscription page.';
  else
    v_allowed := true;
  end if;
  return jsonb_build_object('allowed', v_allowed, 'status', v_status, 'plan_name', v.plan_name,
    'expiry_date', v.expiry_date, 'days_left', v_days, 'reason', v_reason);
end $$;

-- Safe-to-show texts for the login page (works before sign-in).
create or replace function public.get_public_settings() returns jsonb
language sql stable security definer set search_path = ''
as $$
  select coalesce(jsonb_object_agg(s.key, s.value), '{}'::jsonb)
  from public.system_settings s
  where s.key in ('login_help_text', 'forgot_password_text', 'global_announcement',
                  'subscription_reminder_text') and jsonb_typeof(s.value) = 'string'
$$;

create or replace function public.get_labels() returns jsonb
language sql stable security definer set search_path = ''
as $$
  select coalesce((select s.value from public.system_settings s
                   where s.key = 'ui_labels' and jsonb_typeof(s.value) = 'object'), '{}'::jsonb)
$$;

create or replace function public.mark_message_read(p_message_id uuid) returns void
language plpgsql security definer set search_path = ''
as $$
declare v_rid uuid := public._staff_restaurant(array['owner']);
begin
  update public.restaurant_messages set is_read = true
  where id = p_message_id and restaurant_id = v_rid;
end $$;

-- Credit an order to the waiter's commission exactly once (unique order_id).
create or replace function public._record_sale(p_order_id uuid) returns void
language plpgsql security definer set search_path = ''
as $$
declare o public.orders; v_basis text; v_pct numeric(5,2) := 0; v_base numeric(12,2);
begin
  select * into o from public.orders where id = p_order_id and status = 'completed';
  if not found then return; end if;
  select r.commission_basis into v_basis from public.restaurants r where r.id = o.restaurant_id;
  if o.waiter_id is not null then
    select e.commission_pct into v_pct from public.restaurant_employees e where e.id = o.waiter_id;
  end if;
  v_base := case when v_basis = 'net' then o.total else o.subtotal end;
  insert into public.sales_records (restaurant_id, order_id, waiter_id, completed_at, gross_amount,
      net_amount, commission_basis, base_amount, commission_pct, commission_amount)
  values (o.restaurant_id, o.id, o.waiter_id, coalesce(o.completed_at, now()), o.subtotal, o.total,
      v_basis, v_base, coalesce(v_pct, 0), round(v_base * coalesce(v_pct, 0) / 100, 2))
  on conflict (order_id) do nothing;
end $$;

create or replace function public.update_order_status(p_order_id uuid, p_new_status text) returns void
language plpgsql security definer set search_path = ''
as $$
declare
  v_rid uuid; v_role text; o public.orders; v_next text; v_emp uuid; v_wid uuid; v_wname text;
begin
  v_rid := public._staff_restaurant(array['owner', 'waiter']);
  perform public._require_open(v_rid);
  select * into o from public.orders where id = p_order_id and restaurant_id = v_rid for update;
  if not found then
    raise exception 'Order not found.';
  end if;
  select p.role into v_role from public.profiles p where p.id = auth.uid();
  -- A waiter may only work on orders of their own tables.
  if v_role = 'waiter' then
    v_emp := public.auth_employee_id();
    if v_emp is null or o.waiter_id is distinct from v_emp then
      raise exception 'This order belongs to another waiter''s table.';
    end if;
  end if;
  if p_new_status = 'cancelled' then
    raise exception 'Use the cancel action to cancel an order.';
  end if;
  v_next := case o.status
    when 'received' then 'confirmed' when 'confirmed' then 'preparing'
    when 'preparing' then 'ready' when 'ready' then 'served' when 'served' then 'completed'
    else null end;
  if v_next is null or p_new_status is distinct from v_next then
    raise exception 'This order is % and cannot be changed to %.', o.status, coalesce(p_new_status, 'that status');
  end if;
  -- The order always belongs to the waiter of its table, whoever clicks the button.
  -- (Older or unassigned orders pick up the table's current waiter here.)
  v_wid := o.waiter_id; v_wname := o.waiter_name;
  if v_wid is null then
    select t.waiter_id into v_wid from public.restaurant_tables t where t.id = o.table_id;
    if v_wid is null then
      raise exception 'Table % has no waiter yet. Assign a waiter to this table first.', o.table_label;
    end if;
    select e.full_name into v_wname from public.restaurant_employees e where e.id = v_wid;
  end if;
  update public.orders
     set status = p_new_status, waiter_id = v_wid, waiter_name = v_wname,
         completed_at = case when p_new_status = 'completed' then now() else completed_at end
   where id = o.id;
  if p_new_status = 'completed' then
    perform public._record_sale(o.id);
  end if;
end $$;

create or replace function public.cancel_order(p_order_id uuid, p_reason text) returns void
language plpgsql security definer set search_path = ''
as $$
declare v_rid uuid; o public.orders;
begin
  v_rid := public._staff_restaurant(array['owner']);
  perform public._require_open(v_rid);
  select * into o from public.orders where id = p_order_id and restaurant_id = v_rid for update;
  if not found then
    raise exception 'Order not found.';
  end if;
  if o.status in ('completed', 'cancelled') then
    raise exception 'A % order cannot be cancelled. Completed orders can be marked as refunded instead.', o.status;
  end if;
  update public.orders
     set status = 'cancelled', cancelled_at = now(),
         cancel_reason = left(coalesce(nullif(btrim(p_reason), ''), 'No reason given'), 300)
   where id = o.id;
  perform public.log_audit('order.cancel', 'order', o.id, v_rid,
    jsonb_build_object('order_number', o.order_number, 'total', o.total));
end $$;

-- Refund = money returned for a COMPLETED order. It is removed from revenue and
-- its commission is reversed. (Partial refunds are not supported in this version.)
create or replace function public.refund_order(p_order_id uuid, p_reason text) returns void
language plpgsql security definer set search_path = ''
as $$
declare v_rid uuid; o public.orders;
begin
  v_rid := public._staff_restaurant(array['owner']);
  perform public._require_open(v_rid);
  if char_length(btrim(coalesce(p_reason, ''))) = 0 then
    raise exception 'Please enter a reason for the refund.';
  end if;
  select * into o from public.orders where id = p_order_id and restaurant_id = v_rid for update;
  if not found then
    raise exception 'Order not found.';
  end if;
  if o.status <> 'completed' then
    raise exception 'Only completed orders can be refunded.';
  end if;
  if o.refunded_at is not null then
    raise exception 'This order was already refunded.';
  end if;
  update public.orders set refunded_at = now(), refund_reason = left(btrim(p_reason), 300) where id = o.id;
  update public.sales_records set status = 'reversed', reversed_at = now()
   where order_id = o.id and status = 'earned';
  perform public.log_audit('order.refund', 'order', o.id, v_rid,
    jsonb_build_object('order_number', o.order_number, 'total', o.total));
end $$;

create or replace function public.update_request_status(p_request_id uuid, p_new_status text) returns void
language plpgsql security definer set search_path = ''
as $$
declare v_rid uuid; r public.waiter_requests; v_ok boolean; v_role text;
begin
  v_rid := public._staff_restaurant(array['owner', 'waiter']);
  perform public._require_open(v_rid);
  select * into r from public.waiter_requests where id = p_request_id and restaurant_id = v_rid for update;
  if not found then
    raise exception 'Request not found.';
  end if;
  select p.role into v_role from public.profiles p where p.id = auth.uid();
  if v_role = 'waiter' and r.waiter_id is distinct from public.auth_employee_id() then
    raise exception 'This request belongs to another waiter''s table.';
  end if;
  v_ok := case r.status
    when 'pending' then p_new_status in ('accepted', 'cancelled')
    when 'accepted' then p_new_status in ('in_progress', 'completed', 'cancelled')
    when 'in_progress' then p_new_status in ('completed', 'cancelled')
    else false end;
  if not coalesce(v_ok, false) then
    raise exception 'This request is % and cannot be changed to %.', r.status, coalesce(p_new_status, 'that status');
  end if;
  update public.waiter_requests
     set status = p_new_status,
         accepted_at = case when p_new_status = 'accepted' then now() else accepted_at end,
         accepted_by = case when p_new_status = 'accepted' then auth.uid() else accepted_by end,
         completed_at = case when p_new_status in ('completed', 'cancelled') then now() else completed_at end
   where id = r.id;
end $$;

-- Flags pending requests that nobody answered within the restaurant's limit.
-- Called whenever staff refresh the request list (no scheduler needed).
create or replace function public.escalate_stale_requests() returns integer
language plpgsql security definer set search_path = ''
as $$
declare v_rid uuid; v_min integer; v_count integer;
begin
  v_rid := public.auth_restaurant_id();
  if v_rid is null or not public.is_member_of(v_rid) then
    return 0;
  end if;
  select r.escalation_minutes into v_min from public.restaurants r where r.id = v_rid;
  update public.waiter_requests
     set escalated = true, escalated_at = now()
   where restaurant_id = v_rid and status = 'pending' and not escalated
     and created_at < now() - make_interval(mins => v_min);
  get diagnostics v_count = row_count;
  return v_count;
end $$;

-- Give a table to a waiter (or to nobody). Orders and requests that are still open move with
-- the table, so the person serving the table is the person who handles and earns from them.
-- Completed and cancelled orders never change. Internal: callers check permission first.
create or replace function public._move_table(p_rid uuid, p_table_id uuid, p_waiter_id uuid) returns void
language plpgsql security definer set search_path = ''
as $$
declare v_name text;
begin
  if p_waiter_id is not null then
    select e.full_name into v_name from public.restaurant_employees e where e.id = p_waiter_id;
  end if;
  update public.restaurant_tables set waiter_id = p_waiter_id where id = p_table_id and restaurant_id = p_rid;
  update public.orders set waiter_id = p_waiter_id, waiter_name = v_name
   where table_id = p_table_id and restaurant_id = p_rid and status not in ('completed', 'cancelled');
  update public.waiter_requests set waiter_id = p_waiter_id
   where table_id = p_table_id and restaurant_id = p_rid and status in ('pending', 'accepted', 'in_progress');
end $$;

create or replace function public._require_active_waiter(p_rid uuid, p_waiter_id uuid) returns void
language plpgsql stable security definer set search_path = ''
as $$
begin
  if not exists (select 1 from public.restaurant_employees e
                  where e.id = p_waiter_id and e.restaurant_id = p_rid and e.is_active) then
    raise exception 'Please choose an active waiter of your restaurant.';
  end if;
end $$;

create or replace function public.assign_table_waiter(p_table_id uuid, p_waiter_id uuid) returns void
language plpgsql security definer set search_path = ''
as $$
declare v_rid uuid; t public.restaurant_tables;
begin
  v_rid := public._staff_restaurant(array['owner']);
  perform public._require_open(v_rid);
  select * into t from public.restaurant_tables where id = p_table_id and restaurant_id = v_rid for update;
  if not found then
    raise exception 'Table not found.';
  end if;
  if p_waiter_id is not null then
    perform public._require_active_waiter(v_rid, p_waiter_id);
  end if;
  perform public._move_table(v_rid, t.id, p_waiter_id);
  perform public.log_audit('table.assign_waiter', 'table', t.id, v_rid,
    jsonb_build_object('table', t.label, 'from', t.waiter_id, 'to', p_waiter_id));
end $$;

-- Set the full list of tables a waiter looks after. Tables dropped from the list become
-- unassigned; tables added to it are taken from whoever had them.
create or replace function public.set_waiter_tables(p_waiter_id uuid, p_table_ids uuid[]) returns integer
language plpgsql security definer set search_path = ''
as $$
declare v_rid uuid; v_ids uuid[]; v_id uuid; v_n integer;
begin
  v_rid := public._staff_restaurant(array['owner']);
  perform public._require_open(v_rid);
  perform public._require_active_waiter(v_rid, p_waiter_id);
  v_ids := coalesce(p_table_ids, '{}');
  select count(*) into v_n from public.restaurant_tables t where t.restaurant_id = v_rid and t.id = any (v_ids);
  if v_n <> (select count(distinct x) from unnest(v_ids) x) then
    raise exception 'One of the selected tables was not found.';
  end if;
  for v_id in select t.id from public.restaurant_tables t
               where t.restaurant_id = v_rid and t.waiter_id = p_waiter_id and not (t.id = any (v_ids)) loop
    perform public._move_table(v_rid, v_id, null);
  end loop;
  for v_id in select t.id from public.restaurant_tables t
               where t.restaurant_id = v_rid and t.id = any (v_ids) and t.waiter_id is distinct from p_waiter_id loop
    perform public._move_table(v_rid, v_id, p_waiter_id);
  end loop;
  perform public.log_audit('waiter.set_tables', 'employee', p_waiter_id, v_rid,
    jsonb_build_object('table_count', coalesce(array_length(v_ids, 1), 0)));
  return coalesce(array_length(v_ids, 1), 0);
end $$;

-- Waiter on leave: hand ALL of their tables to another waiter in one step.
create or replace function public.reassign_waiter_tables(p_from_waiter uuid, p_to_waiter uuid) returns integer
language plpgsql security definer set search_path = ''
as $$
declare v_rid uuid; v_id uuid; v_n integer := 0;
begin
  v_rid := public._staff_restaurant(array['owner']);
  perform public._require_open(v_rid);
  if p_from_waiter is null or p_to_waiter is null or p_from_waiter = p_to_waiter then
    raise exception 'Please choose two different waiters.';
  end if;
  if not exists (select 1 from public.restaurant_employees e where e.id = p_from_waiter and e.restaurant_id = v_rid) then
    raise exception 'Waiter not found.';
  end if;
  perform public._require_active_waiter(v_rid, p_to_waiter);
  for v_id in select t.id from public.restaurant_tables t
               where t.restaurant_id = v_rid and t.waiter_id = p_from_waiter loop
    perform public._move_table(v_rid, v_id, p_to_waiter);
    v_n := v_n + 1;
  end loop;
  perform public.log_audit('waiter.reassign_tables', 'employee', p_from_waiter, v_rid,
    jsonb_build_object('to', p_to_waiter, 'tables', v_n));
  return v_n;
end $$;

-- Replace a table's QR token. The old QR code stops working immediately.
create or replace function public.regenerate_table_token(p_table_id uuid) returns text
language plpgsql security definer set search_path = ''
as $$
declare v_rid uuid; v_token text;
begin
  v_rid := public._staff_restaurant(array['owner']);
  perform public._require_open(v_rid);
  update public.restaurant_tables
     set token = replace(gen_random_uuid()::text || gen_random_uuid()::text, '-', '')
   where id = p_table_id and restaurant_id = v_rid
  returning token into v_token;
  if v_token is null then
    raise exception 'Table not found.';
  end if;
  perform public.log_audit('table.regenerate_token', 'restaurant_table', p_table_id, v_rid);
  return v_token;
end $$;

-- Manual stock movement. Stock can never go below zero. (Automatic deduction
-- from orders is NOT implemented in this version.)
create or replace function public.adjust_stock(p_item_id uuid, p_kind text, p_quantity numeric, p_reason text)
returns numeric
language plpgsql security definer set search_path = ''
as $$
declare v_rid uuid; i public.inventory; v_new numeric(14,2);
begin
  v_rid := public._staff_restaurant(array['owner']);
  perform public._require_open(v_rid);
  if p_kind not in ('addition', 'deduction') then
    raise exception 'Invalid stock action.';
  end if;
  if p_quantity is null or p_quantity <= 0 or p_quantity > 10000000 then
    raise exception 'Quantity must be greater than zero.';
  end if;
  select * into i from public.inventory where id = p_item_id and restaurant_id = v_rid for update;
  if not found then
    raise exception 'Inventory item not found.';
  end if;
  v_new := case when p_kind = 'addition' then i.quantity + p_quantity else i.quantity - p_quantity end;
  if v_new < 0 then
    raise exception 'Not enough stock. Only % % available.', i.quantity, i.unit;
  end if;
  update public.inventory set quantity = v_new where id = i.id;
  insert into public.inventory_transactions (restaurant_id, inventory_id, kind, quantity, balance_after, reason, created_by)
  values (v_rid, i.id, p_kind, p_quantity, v_new, nullif(left(btrim(coalesce(p_reason, '')), 200), ''), auth.uid());
  return v_new;
end $$;

-- Owner files a payment receipt. It is stored as PENDING. Only the Super Admin
-- can approve it (admin_review_payment). Works even when the subscription has
-- expired, so owners can always pay.
create or replace function public.submit_payment_proof(
  p_plan_code text, p_amount numeric, p_reference text, p_note text, p_proof_path text
) returns uuid
language plpgsql security definer set search_path = ''
as $$
declare v_rid uuid; v_plan public.subscription_plans; v_id uuid;
begin
  v_rid := public._staff_restaurant(array['owner']);
  select * into v_plan from public.subscription_plans where code = p_plan_code and is_active and price_pkr > 0;
  if not found then
    raise exception 'That plan is not available for payment.';
  end if;
  if p_amount is null or p_amount <= 0 or p_amount > 100000000 then
    raise exception 'Please enter the amount you paid.';
  end if;
  if p_proof_path is null or p_proof_path not like v_rid::text || '/%' or char_length(p_proof_path) > 200 then
    raise exception 'The receipt file is not valid.';
  end if;
  if not exists (select 1 from storage.objects o where o.bucket_id = 'payment-proofs' and o.name = p_proof_path) then
    raise exception 'The receipt upload could not be found. Please upload it again.';
  end if;
  if (select count(*) from public.subscription_payments
       where restaurant_id = v_rid and status = 'pending') >= 5 then
    raise exception 'You already have several payments waiting for review. Please wait for them to be reviewed.';
  end if;
  insert into public.subscription_payments (restaurant_id, plan_code, amount, reference, note, proof_path)
  values (v_rid, p_plan_code, p_amount, nullif(left(btrim(coalesce(p_reference, '')), 100), ''),
          nullif(left(btrim(coalesce(p_note, '')), 500), ''), p_proof_path)
  returning id into v_id;
  perform public.log_audit('payment.submit', 'subscription_payment', v_id, v_rid,
    jsonb_build_object('plan', p_plan_code, 'amount', p_amount));
  return v_id;
end $$;

-- ---- Dashboard and reports ---------------------------------------------------
-- Definitions used everywhere:
--   REVENUE  = total of COMPLETED orders that were not refunded, counted on the
--              day the order was completed (Pakistan time).
--   ORDERS   = orders placed (by created time), cancelled ones excluded on the
--              dashboard but shown separately in analytics.

create or replace function public.dashboard_summary() returns jsonb
language plpgsql stable security definer set search_path = ''
as $$
declare
  v_rid uuid; d date := public.pkt_today();
  v_today_start timestamptz; v_tomorrow timestamptz; v_week timestamptz; v_month timestamptz;
begin
  v_rid := public._staff_restaurant(array['owner']);
  v_today_start := public.pkt_start(d);
  v_tomorrow := public.pkt_start(d + 1);
  v_week := public.pkt_start(d - 6);
  v_month := public.pkt_start(d - (extract(day from d)::integer - 1));
  return jsonb_build_object(
    'today_orders', (select count(*) from public.orders o
                      where o.restaurant_id = v_rid and o.status <> 'cancelled'
                        and o.created_at >= v_today_start and o.created_at < v_tomorrow),
    'today_revenue', coalesce((select sum(o.total) from public.orders o
                      where o.restaurant_id = v_rid and o.status = 'completed' and o.refunded_at is null
                        and o.completed_at >= v_today_start and o.completed_at < v_tomorrow), 0),
    'week_revenue', coalesce((select sum(o.total) from public.orders o
                      where o.restaurant_id = v_rid and o.status = 'completed' and o.refunded_at is null
                        and o.completed_at >= v_week and o.completed_at < v_tomorrow), 0),
    'month_revenue', coalesce((select sum(o.total) from public.orders o
                      where o.restaurant_id = v_rid and o.status = 'completed' and o.refunded_at is null
                        and o.completed_at >= v_month and o.completed_at < v_tomorrow), 0),
    'pending_orders', (select count(*) from public.orders o where o.restaurant_id = v_rid
                        and o.status in ('received', 'confirmed', 'preparing', 'ready', 'served')),
    'pending_requests', (select count(*) from public.waiter_requests q where q.restaurant_id = v_rid
                          and q.status in ('pending', 'accepted', 'in_progress')),
    'low_stock_count', (select count(*) from public.inventory i where i.restaurant_id = v_rid
                         and i.quantity <= i.reorder_threshold),
    'unread_messages', (select count(*) from public.restaurant_messages m
                         where m.restaurant_id = v_rid and not m.is_read),
    'recent_orders', coalesce((select jsonb_agg(jsonb_build_object('order_number', x.order_number,
                         'table_label', x.table_label, 'status', x.status, 'total', x.total,
                         'created_at', x.created_at) order by x.created_at desc)
                       from (select o.order_number, o.table_label, o.status, o.total, o.created_at
                               from public.orders o where o.restaurant_id = v_rid
                              order by o.created_at desc limit 8) x), '[]'::jsonb)
  );
end $$;

create or replace function public.analytics(p_from date, p_to date) returns jsonb
language plpgsql stable security definer set search_path = ''
as $$
declare
  v_rid uuid; v_lo timestamptz; v_hi timestamptz;
  v_orders integer; v_completed integer; v_cancelled integer; v_revenue numeric; v_expenses numeric;
  v_cogs numeric; v_missing integer; v_profit numeric; v_note text;
begin
  v_rid := public._staff_restaurant(array['owner']);
  if p_from is null or p_to is null or p_from > p_to then
    raise exception 'Please choose a valid date range.';
  end if;
  if p_to - p_from > 366 then
    raise exception 'Please choose a range of at most one year.';
  end if;
  v_lo := public.pkt_start(p_from);
  v_hi := public.pkt_start(p_to + 1);

  select count(*) into v_orders from public.orders o
   where o.restaurant_id = v_rid and o.created_at >= v_lo and o.created_at < v_hi;
  select count(*) into v_cancelled from public.orders o
   where o.restaurant_id = v_rid and o.status = 'cancelled' and o.cancelled_at >= v_lo and o.cancelled_at < v_hi;
  select count(*), coalesce(sum(o.total), 0) into v_completed, v_revenue from public.orders o
   where o.restaurant_id = v_rid and o.status = 'completed' and o.refunded_at is null
     and o.completed_at >= v_lo and o.completed_at < v_hi;
  select coalesce(sum(e.amount), 0) into v_expenses from public.expenses e
   where e.restaurant_id = v_rid and e.expense_date between p_from and p_to;
  select coalesce(sum(oi.unit_cost * oi.quantity), 0), count(distinct oi.name) filter (where oi.unit_cost is null)
    into v_cogs, v_missing
  from public.order_items oi join public.orders o on o.id = oi.order_id
  where o.restaurant_id = v_rid and o.status = 'completed' and o.refunded_at is null
    and o.completed_at >= v_lo and o.completed_at < v_hi;

  if v_completed = 0 then
    v_profit := null;
    v_note := 'No completed sales in this period, so profit cannot be estimated.';
  elsif v_missing > 0 then
    v_profit := null;
    v_note := format('Profit is not shown because %s product(s) sold in this period have no cost price. Add cost prices in the Menu page.', v_missing);
  else
    v_profit := v_revenue - v_cogs - v_expenses;
    v_note := 'Estimated profit = revenue − product cost prices − recorded expenses. If you record ingredient purchases as expenses AND set product cost prices, those costs are counted twice.';
  end if;

  return jsonb_build_object(
    'kpis', jsonb_build_object('orders', v_orders, 'completed', v_completed, 'cancelled', v_cancelled,
        'revenue', v_revenue, 'avg_order_value', case when v_completed > 0 then round(v_revenue / v_completed, 2) else 0 end,
        'expenses', v_expenses, 'estimated_profit', v_profit),
    'profit_note', v_note,
    'daily', coalesce((
      select jsonb_agg(jsonb_build_object('date', to_char(g.d, 'YYYY-MM-DD'), 'revenue', coalesce(s.rev, 0),
                                          'orders', coalesce(s.cnt, 0)) order by g.d)
      from generate_series(p_from::timestamp, p_to::timestamp, interval '1 day') as gs(ts)
      cross join lateral (select gs.ts::date as d) g
      left join (select (o.completed_at at time zone 'Asia/Karachi')::date as d, sum(o.total) as rev, count(*) as cnt
                   from public.orders o
                  where o.restaurant_id = v_rid and o.status = 'completed' and o.refunded_at is null
                    and o.completed_at >= v_lo and o.completed_at < v_hi
                  group by 1) s on s.d = g.d), '[]'::jsonb),
    'top_items', coalesce((
      select jsonb_agg(jsonb_build_object('name', x.name, 'quantity', x.qty, 'revenue', x.rev) order by x.qty desc, x.name)
      from (select oi.name, sum(oi.quantity)::integer as qty, sum(oi.line_total) as rev
              from public.order_items oi join public.orders o on o.id = oi.order_id
             where o.restaurant_id = v_rid and o.status = 'completed' and o.refunded_at is null
               and o.completed_at >= v_lo and o.completed_at < v_hi
             group by oi.name order by qty desc, oi.name limit 10) x), '[]'::jsonb),
    'waiter_sales', coalesce((
      select jsonb_agg(jsonb_build_object('waiter', x.waiter, 'orders', x.cnt, 'sales', x.sales) order by x.sales desc)
      from (select coalesce(o.waiter_name, 'Unassigned') as waiter, count(*) as cnt, sum(o.total) as sales
              from public.orders o
             where o.restaurant_id = v_rid and o.status = 'completed' and o.refunded_at is null
               and o.completed_at >= v_lo and o.completed_at < v_hi
             group by 1) x), '[]'::jsonb),
    'low_stock', coalesce((
      select jsonb_agg(jsonb_build_object('name', i.name, 'quantity', i.quantity, 'unit', i.unit) order by i.name)
      from public.inventory i where i.restaurant_id = v_rid and i.quantity <= i.reorder_threshold), '[]'::jsonb)
  );
end $$;

-- Personal statistics for a waiter. Sales and commission are returned only when
-- the owner allowed it in the restaurant profile.
create or replace function public.waiter_stats(p_from date, p_to date) returns jsonb
language plpgsql stable security definer set search_path = ''
as $$
declare
  v_rid uuid; v_role text; v_emp uuid; v_lo timestamptz; v_hi timestamptz;
  r public.restaurants; v_see_sales boolean; v_see_comm boolean;
  v_orders integer; v_completed integer; v_cancelled integer; v_requests integer;
  v_sales numeric := 0; v_comm numeric := 0;
begin
  v_rid := public._staff_restaurant(array['owner', 'waiter']);
  if p_from is null or p_to is null or p_from > p_to or p_to - p_from > 366 then
    raise exception 'Please choose a valid date range.';
  end if;
  v_lo := public.pkt_start(p_from);
  v_hi := public.pkt_start(p_to + 1);
  select p.role into v_role from public.profiles p where p.id = auth.uid();
  select * into r from public.restaurants where id = v_rid;
  if v_role = 'waiter' then
    select e.id into v_emp from public.restaurant_employees e where e.user_id = auth.uid();
    v_see_sales := r.waiter_can_view_sales;
    v_see_comm := r.waiter_can_view_commission;
  else
    v_see_sales := true; v_see_comm := true;
  end if;

  select count(*) into v_orders from public.orders o
   where o.restaurant_id = v_rid and (v_role <> 'waiter' or o.waiter_id = v_emp)
     and o.created_at >= v_lo and o.created_at < v_hi;
  select count(*) into v_cancelled from public.orders o
   where o.restaurant_id = v_rid and (v_role <> 'waiter' or o.waiter_id = v_emp)
     and o.status = 'cancelled' and o.cancelled_at >= v_lo and o.cancelled_at < v_hi;
  select count(*), coalesce(sum(o.total), 0) into v_completed, v_sales from public.orders o
   where o.restaurant_id = v_rid and (v_role <> 'waiter' or o.waiter_id = v_emp)
     and o.status = 'completed' and o.refunded_at is null
     and o.completed_at >= v_lo and o.completed_at < v_hi;
  select count(*) into v_requests from public.waiter_requests q
   where q.restaurant_id = v_rid and (v_role <> 'waiter' or q.accepted_by = auth.uid())
     and q.accepted_at >= v_lo and q.accepted_at < v_hi;
  select coalesce(sum(sr.commission_amount), 0) into v_comm from public.sales_records sr
   where sr.restaurant_id = v_rid and sr.status = 'earned' and (v_role <> 'waiter' or sr.waiter_id = v_emp)
     and sr.completed_at >= v_lo and sr.completed_at < v_hi;

  return jsonb_build_object('orders', v_orders, 'completed', v_completed, 'cancelled', v_cancelled,
    'requests_handled', v_requests,
    'sales', case when v_see_sales then v_sales else 0 end,
    'commission', case when v_see_comm then v_comm else 0 end,
    'sales_visible', v_see_sales, 'commission_visible', v_see_comm);
end $$;

-- A waiter's own performance: daily trend, best-selling dishes, busiest tables and hours, speed.
-- Only the waiter's own tables are counted. Money fields are null unless the owner allows them.
create or replace function public.waiter_analytics(p_from date, p_to date) returns jsonb
language plpgsql stable security definer set search_path = ''
as $$
declare
  v_rid uuid; v_emp uuid; v_lo timestamptz; v_hi timestamptz; r public.restaurants;
  v_sales boolean; v_comm boolean; v_pct numeric;
begin
  v_rid := public._staff_restaurant(array['waiter']);
  v_emp := public.auth_employee_id();
  if v_emp is null then
    raise exception 'permission denied' using errcode = '42501';
  end if;
  if p_from is null or p_to is null or p_from > p_to or p_to - p_from > 366 then
    raise exception 'Please choose a valid date range.';
  end if;
  v_lo := public.pkt_start(p_from);
  v_hi := public.pkt_start(p_to + 1);
  select * into r from public.restaurants where id = v_rid;
  v_sales := r.waiter_can_view_sales;
  v_comm := r.waiter_can_view_commission;
  select e.commission_pct into v_pct from public.restaurant_employees e where e.id = v_emp;

  return jsonb_build_object(
    'sales_visible', v_sales, 'commission_visible', v_comm,
    'commission_pct', case when v_comm then v_pct else null end,
    'avg_order_value', case when v_sales then (
        select round(avg(o.total), 2) from public.orders o
         where o.waiter_id = v_emp and o.status = 'completed' and o.refunded_at is null
           and o.completed_at >= v_lo and o.completed_at < v_hi) else null end,
    'refunded_orders', (select count(*) from public.orders o
                         where o.waiter_id = v_emp and o.refunded_at is not null
                           and o.completed_at >= v_lo and o.completed_at < v_hi),
    'commission_reversed', case when v_comm then coalesce((
        select sum(sr.commission_amount) from public.sales_records sr
         where sr.waiter_id = v_emp and sr.status = 'reversed'
           and sr.completed_at >= v_lo and sr.completed_at < v_hi), 0) else null end,
    'avg_minutes_to_complete', (
        select round((avg(extract(epoch from (o.completed_at - o.created_at))) / 60)::numeric, 1)
          from public.orders o
         where o.waiter_id = v_emp and o.status = 'completed'
           and o.completed_at >= v_lo and o.completed_at < v_hi),
    'avg_minutes_to_accept_request', (
        select round((avg(extract(epoch from (q.accepted_at - q.created_at))) / 60)::numeric, 1)
          from public.waiter_requests q
         where q.waiter_id = v_emp and q.accepted_at is not null
           and q.accepted_at >= v_lo and q.accepted_at < v_hi),
    'daily', coalesce((
      select jsonb_agg(jsonb_build_object(
               'date', to_char(g.d, 'YYYY-MM-DD'),
               'orders', coalesce(o.cnt, 0),
               'sales', case when v_sales then coalesce(o.sales, 0) else null end,
               'commission', case when v_comm then coalesce(c.amt, 0) else null end) order by g.d)
      from generate_series(p_from::timestamp, p_to::timestamp, interval '1 day') g(d)
      left join (select (x.completed_at at time zone 'Asia/Karachi')::date as d, count(*) as cnt, sum(x.total) as sales
                   from public.orders x
                  where x.waiter_id = v_emp and x.status = 'completed' and x.refunded_at is null
                    and x.completed_at >= v_lo and x.completed_at < v_hi
                  group by 1) o on o.d = g.d::date
      left join (select (sr.completed_at at time zone 'Asia/Karachi')::date as d, sum(sr.commission_amount) as amt
                   from public.sales_records sr
                  where sr.waiter_id = v_emp and sr.status = 'earned'
                    and sr.completed_at >= v_lo and sr.completed_at < v_hi
                  group by 1) c on c.d = g.d::date), '[]'::jsonb),
    'top_items', coalesce((
      select jsonb_agg(jsonb_build_object('name', t.name, 'quantity', t.qty,
               'sales', case when v_sales then t.sales else null end) order by t.qty desc, t.name)
      from (select i.name, sum(i.quantity) as qty, sum(i.line_total) as sales
              from public.order_items i join public.orders o on o.id = i.order_id
             where o.waiter_id = v_emp and o.status = 'completed' and o.refunded_at is null
               and o.completed_at >= v_lo and o.completed_at < v_hi
             group by i.name order by sum(i.quantity) desc, i.name limit 8) t), '[]'::jsonb),
    'by_table', coalesce((
      select jsonb_agg(jsonb_build_object('table', t.table_label, 'orders', t.cnt,
               'sales', case when v_sales then t.sales else null end) order by t.cnt desc, t.table_label)
      from (select o.table_label, count(*) as cnt, sum(o.total) as sales
              from public.orders o
             where o.waiter_id = v_emp and o.status = 'completed' and o.refunded_at is null
               and o.completed_at >= v_lo and o.completed_at < v_hi
             group by o.table_label order by count(*) desc, o.table_label limit 10) t), '[]'::jsonb),
    'by_hour', coalesce((
      select jsonb_agg(jsonb_build_object('hour', h.hr, 'orders', h.cnt) order by h.hr)
      from (select extract(hour from o.created_at at time zone 'Asia/Karachi')::integer as hr, count(*) as cnt
              from public.orders o
             where o.waiter_id = v_emp and o.status <> 'cancelled'
               and o.created_at >= v_lo and o.created_at < v_hi
             group by 1) h), '[]'::jsonb)
  );
end $$;

-- ---- Commission --------------------------------------------------------------
-- Rules: only COMPLETED orders earn commission, once per order (sales_records.order_id
-- is unique). Cancelled orders never earn. A refunded order's commission is
-- reversed. Gross = order subtotal, Net = subtotal minus discounts; the owner's
-- choice at the time the order was completed is stored on each record.

create or replace function public.commission_report(p_from date, p_to date, p_waiter_id uuid default null)
returns jsonb
language plpgsql stable security definer set search_path = ''
as $$
declare v_rid uuid; v_lo timestamptz; v_hi timestamptz; v_basis text;
begin
  v_rid := public._staff_restaurant(array['owner']);
  if p_from is null or p_to is null or p_from > p_to or p_to - p_from > 366 then
    raise exception 'Please choose a valid date range.';
  end if;
  v_lo := public.pkt_start(p_from);
  v_hi := public.pkt_start(p_to + 1);
  select r.commission_basis into v_basis from public.restaurants r where r.id = v_rid;
  return jsonb_build_object(
    'basis', v_basis,
    'rows', coalesce((
      select jsonb_agg(jsonb_build_object('waiter', x.waiter, 'commission_pct', x.pct, 'orders', x.cnt,
                                          'eligible_sales', x.sales, 'commission', x.comm) order by x.comm desc, x.waiter)
      from (select e.full_name as waiter, count(*) as cnt, sum(sr.base_amount) as sales,
                   sum(sr.commission_amount) as comm,
                   case when sum(sr.base_amount) > 0 then round(sum(sr.commission_amount) / sum(sr.base_amount) * 100, 2)
                        else e.commission_pct end as pct
              from public.sales_records sr
              join public.restaurant_employees e on e.id = sr.waiter_id
             where sr.restaurant_id = v_rid and sr.status = 'earned'
               and sr.completed_at >= v_lo and sr.completed_at < v_hi
               and (p_waiter_id is null or sr.waiter_id = p_waiter_id)
             group by e.id, e.full_name, e.commission_pct) x), '[]'::jsonb),
    'totals', (select jsonb_build_object('eligible_sales', coalesce(sum(sr.base_amount), 0),
                      'orders', count(*), 'commission', coalesce(sum(sr.commission_amount), 0))
                 from public.sales_records sr
                where sr.restaurant_id = v_rid and sr.status = 'earned' and sr.waiter_id is not null
                  and sr.completed_at >= v_lo and sr.completed_at < v_hi
                  and (p_waiter_id is null or sr.waiter_id = p_waiter_id))
  );
end $$;

create or replace function public.commission_history(p_from date, p_to date, p_waiter_id uuid default null)
returns jsonb
language plpgsql stable security definer set search_path = ''
as $$
declare v_rid uuid; v_lo timestamptz; v_hi timestamptz;
begin
  v_rid := public._staff_restaurant(array['owner']);
  if p_from is null or p_to is null or p_from > p_to or p_to - p_from > 366 then
    raise exception 'Please choose a valid date range.';
  end if;
  v_lo := public.pkt_start(p_from);
  v_hi := public.pkt_start(p_to + 1);
  return coalesce((
    select jsonb_agg(jsonb_build_object('completed_at', x.completed_at, 'order_number', x.order_number,
        'waiter', x.waiter, 'base_amount', x.base_amount, 'commission_pct', x.commission_pct,
        'commission', x.commission_amount, 'status', x.status) order by x.completed_at desc)
    from (select sr.completed_at, o.order_number, e.full_name as waiter, sr.base_amount,
                 sr.commission_pct, sr.commission_amount, sr.status
            from public.sales_records sr
            join public.orders o on o.id = sr.order_id
            join public.restaurant_employees e on e.id = sr.waiter_id
           where sr.restaurant_id = v_rid and sr.completed_at >= v_lo and sr.completed_at < v_hi
             and (p_waiter_id is null or sr.waiter_id = p_waiter_id)
           order by sr.completed_at desc limit 1000) x), '[]'::jsonb);
end $$;

-- -----------------------------------------------------------------------------
-- 6. CUSTOMER (QR) FUNCTIONS  -  callable WITHOUT signing in
--    The browser sends only the table's secret token. Restaurant, table, item
--    names and prices all come from the database. Totals are computed here.
-- -----------------------------------------------------------------------------

create or replace function public._customer_table(p_token text, p_require_open boolean default true)
returns public.restaurant_tables
language plpgsql stable security definer set search_path = ''
as $$
declare t public.restaurant_tables;
begin
  if p_token is null or p_token !~ '^[A-Za-z0-9_-]{16,128}$' then
    raise exception 'This QR code is not valid.';
  end if;
  select * into t from public.restaurant_tables where token = p_token and is_active;
  if not found then
    raise exception 'This QR code is not valid.';
  end if;
  if p_require_open and not public.restaurant_is_open(t.restaurant_id) then
    raise exception 'This restaurant is not taking orders right now.';
  end if;
  return t;
end $$;

create or replace function public.customer_get_menu(p_token text) returns jsonb
language plpgsql stable security definer set search_path = ''
as $$
declare t public.restaurant_tables; r public.restaurants;
begin
  t := public._customer_table(p_token);
  select * into r from public.restaurants where id = t.restaurant_id;
  return jsonb_build_object(
    'restaurant', jsonb_build_object('name', r.name, 'logo_url', r.logo_url, 'phone', r.phone),
    'table', jsonb_build_object('label', t.label),
    'categories', coalesce((select jsonb_agg(jsonb_build_object('id', c.id, 'name', c.name) order by c.sort_order, c.name)
                              from public.menu_categories c where c.restaurant_id = r.id and c.is_active), '[]'::jsonb),
    'items', coalesce((select jsonb_agg(jsonb_build_object('id', i.id, 'category_id', i.category_id, 'name', i.name,
                          'description', i.description, 'price', i.price, 'image_url', i.image_url,
                          'is_available', i.is_available, 'is_featured', i.is_featured, 'is_popular', i.is_popular)
                          order by i.name)
                         from public.menu_items i
                         join public.menu_categories c on c.id = i.category_id and c.is_active
                        where i.restaurant_id = r.id and i.is_active), '[]'::jsonb)
  );
end $$;

-- p_items: [{"item_id": "<uuid>", "qty": 2, "note": "no onions"}, ...]
-- p_idempotency_key: a random string the page makes once per cart; submitting
-- the same key twice returns the first order instead of creating a duplicate.
create or replace function public.customer_place_order(
  p_token text, p_items jsonb, p_notes text default null, p_idempotency_key text default null
) returns jsonb
language plpgsql security definer set search_path = ''
as $$
declare
  t public.restaurant_tables; v_notes text; v_key text; v_dup record; elem jsonb;
  v_qty integer; v_total_qty integer := 0; v_item_id uuid; v_name text; v_price numeric(12,2); v_cost numeric(12,2);
  v_ids uuid[] := '{}'; v_qtys integer[] := '{}'; v_names text[] := '{}'; v_prices numeric[] := '{}';
  v_costs numeric[] := '{}'; v_lnotes text[] := '{}';
  v_subtotal numeric(12,2) := 0; v_num bigint; v_order_id uuid; v_recent integer;
begin
  t := public._customer_table(p_token);

  v_notes := nullif(btrim(coalesce(p_notes, '')), '');
  if char_length(coalesce(v_notes, '')) > 500 then
    raise exception 'Your note is too long (maximum 500 characters).';
  end if;
  v_key := nullif(btrim(coalesce(p_idempotency_key, '')), '');
  if v_key is not null and char_length(v_key) not between 8 and 80 then
    raise exception 'Something went wrong. Please refresh the page and try again.';
  end if;

  if v_key is not null then
    select o.id, o.order_number, o.status, o.total into v_dup
      from public.orders o where o.restaurant_id = t.restaurant_id and o.idempotency_key = v_key;
    if found then
      return jsonb_build_object('order_id', v_dup.id, 'order_number', v_dup.order_number, 'status', v_dup.status,
                                'total', v_dup.total, 'table_label', t.label, 'duplicate', true);
    end if;
  end if;

  if p_items is null or jsonb_typeof(p_items) <> 'array' or jsonb_array_length(p_items) = 0 then
    raise exception 'Your cart is empty.';
  end if;
  if jsonb_array_length(p_items) > 40 then
    raise exception 'Too many different items in one order (maximum 40).';
  end if;

  select count(*) into v_recent from public.orders o
   where o.table_id = t.id and o.created_at > now() - interval '10 minutes';
  if v_recent >= 8 then
    raise exception 'This table has sent several orders very recently. Please ask a waiter for help.';
  end if;

  for elem in select * from jsonb_array_elements(p_items) loop
    if jsonb_typeof(elem) <> 'object'
       or coalesce(elem ->> 'item_id', '') !~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
       or coalesce(jsonb_typeof(elem -> 'qty'), '') <> 'number'
       or coalesce(elem ->> 'qty', '') !~ '^[0-9]{1,3}$' then
      raise exception 'One of the items in your cart is not valid. Please refresh the page.';
    end if;
    v_qty := (elem ->> 'qty')::integer;
    if v_qty < 1 or v_qty > 20 then
      raise exception 'Each item can be ordered 1 to 20 times.';
    end if;
    v_total_qty := v_total_qty + v_qty;
    if v_total_qty > 100 then
      raise exception 'That is too many items for one order. Please ask a waiter for help.';
    end if;
    if char_length(coalesce(elem ->> 'note', '')) > 200 then
      raise exception 'An item note is too long (maximum 200 characters).';
    end if;
    v_item_id := (elem ->> 'item_id')::uuid;

    -- Price, name and availability always come from the database.
    select i.name, i.price, i.cost_price into v_name, v_price, v_cost
      from public.menu_items i
      join public.menu_categories c on c.id = i.category_id and c.is_active
     where i.id = v_item_id and i.restaurant_id = t.restaurant_id and i.is_active and i.is_available;
    if not found then
      select i.name into v_name from public.menu_items i where i.id = v_item_id and i.restaurant_id = t.restaurant_id;
      raise exception 'Sorry, % is no longer available. Please remove it from your cart and try again.',
        coalesce('"' || v_name || '"', 'one of your items');
    end if;
    v_ids := array_append(v_ids, v_item_id);
    v_qtys := array_append(v_qtys, v_qty);
    v_names := array_append(v_names, v_name);
    v_prices := array_append(v_prices, v_price::numeric);
    v_costs := array_append(v_costs, v_cost::numeric);
    v_lnotes := array_append(v_lnotes, nullif(btrim(coalesce(elem ->> 'note', '')), ''));
    v_subtotal := v_subtotal + v_price * v_qty;
  end loop;

  begin
    update public.restaurants set next_order_number = next_order_number + 1
     where id = t.restaurant_id returning next_order_number - 1 into v_num;
    insert into public.orders (restaurant_id, order_number, table_id, table_label, waiter_id, waiter_name, status,
                               subtotal, discount, total, notes, idempotency_key)
    values (t.restaurant_id, v_num, t.id, t.label, t.waiter_id,
            (select e.full_name from public.restaurant_employees e where e.id = t.waiter_id),
            'received', v_subtotal, 0, v_subtotal, v_notes, v_key)
    returning id into v_order_id;
  exception when unique_violation then
    -- Two identical submissions raced each other: return the winner.
    select o.id, o.order_number, o.status, o.total into v_dup
      from public.orders o where o.restaurant_id = t.restaurant_id and o.idempotency_key = v_key;
    if found then
      return jsonb_build_object('order_id', v_dup.id, 'order_number', v_dup.order_number, 'status', v_dup.status,
                                'total', v_dup.total, 'table_label', t.label, 'duplicate', true);
    end if;
    raise;
  end;

  insert into public.order_items (order_id, restaurant_id, menu_item_id, name, unit_price, unit_cost, quantity, line_total, note)
  select v_order_id, t.restaurant_id, v_ids[g], v_names[g], v_prices[g], v_costs[g], v_qtys[g],
         v_prices[g] * v_qtys[g], v_lnotes[g]
    from generate_series(1, array_length(v_ids, 1)) g;

  return jsonb_build_object('order_id', v_order_id, 'order_number', v_num, 'status', 'received',
                            'total', v_subtotal, 'table_label', t.label, 'duplicate', false);
end $$;

-- Status of this table's orders (the page remembers its own order ids).
create or replace function public.customer_get_orders(p_token text, p_order_ids uuid[]) returns jsonb
language plpgsql stable security definer set search_path = ''
as $$
declare t public.restaurant_tables;
begin
  t := public._customer_table(p_token, false);
  if p_order_ids is null or cardinality(p_order_ids) = 0 then
    return '[]'::jsonb;
  end if;
  if cardinality(p_order_ids) > 10 then
    raise exception 'Too many orders requested.';
  end if;
  return coalesce((
    select jsonb_agg(jsonb_build_object('order_id', o.id, 'order_number', o.order_number, 'status', o.status,
        'total', o.total, 'created_at', o.created_at,
        'items', (select coalesce(jsonb_agg(jsonb_build_object('name', oi.name, 'quantity', oi.quantity,
                          'line_total', oi.line_total) order by oi.name), '[]'::jsonb)
                    from public.order_items oi where oi.order_id = o.id)) order by o.created_at desc)
      from public.orders o
     where o.id = any (p_order_ids) and o.table_id = t.id
       and o.created_at > now() - interval '24 hours'), '[]'::jsonb);
end $$;

create or replace function public.customer_create_request(p_token text, p_type text, p_note text default null)
returns jsonb
language plpgsql security definer set search_path = ''
as $$
declare t public.restaurant_tables; v_note text; v_open record; v_id uuid;
begin
  t := public._customer_table(p_token);
  if p_type is null or p_type not in ('call_waiter', 'bill', 'water', 'cutlery', 'assistance', 'other') then
    raise exception 'Invalid request type.';
  end if;
  v_note := nullif(btrim(coalesce(p_note, '')), '');
  if char_length(coalesce(v_note, '')) > 200 then
    raise exception 'Your note is too long (maximum 200 characters).';
  end if;
  select q.id, q.status into v_open from public.waiter_requests q
   where q.table_id = t.id and q.request_type = p_type and q.status in ('pending', 'accepted', 'in_progress');
  if found then
    return jsonb_build_object('id', v_open.id, 'status', v_open.status, 'duplicate', true);
  end if;
  if (select count(*) from public.waiter_requests q
       where q.table_id = t.id and q.created_at > now() - interval '10 minutes') >= 10 then
    raise exception 'This table has sent many requests very recently. A waiter is on the way.';
  end if;
  begin
    insert into public.waiter_requests (restaurant_id, table_id, table_label, waiter_id, request_type, note)
    values (t.restaurant_id, t.id, t.label, t.waiter_id, p_type, v_note) returning id into v_id;
  exception when unique_violation then
    select q.id, q.status into v_open from public.waiter_requests q
     where q.table_id = t.id and q.request_type = p_type and q.status in ('pending', 'accepted', 'in_progress');
    return jsonb_build_object('id', v_open.id, 'status', v_open.status, 'duplicate', true);
  end;
  return jsonb_build_object('id', v_id, 'status', 'pending', 'duplicate', false);
end $$;

create or replace function public.customer_get_requests(p_token text) returns jsonb
language plpgsql stable security definer set search_path = ''
as $$
declare t public.restaurant_tables;
begin
  t := public._customer_table(p_token, false);
  return coalesce((
    select jsonb_agg(jsonb_build_object('id', x.id, 'request_type', x.request_type, 'status', x.status,
                                        'created_at', x.created_at) order by x.created_at desc)
      from (select q.id, q.request_type, q.status, q.created_at from public.waiter_requests q
             where q.table_id = t.id and q.created_at > now() - interval '4 hours'
             order by q.created_at desc limit 10) x), '[]'::jsonb);
end $$;

-- -----------------------------------------------------------------------------
-- 7. SUPER ADMIN FUNCTIONS (each one starts by checking the caller is Super Admin)
-- -----------------------------------------------------------------------------

-- Renews or switches a subscription: new expiry = later of (today, current expiry) + days.
create or replace function public._apply_renewal(p_rid uuid, p_plan_code text, p_days integer, p_actor uuid)
returns date
language plpgsql security definer set search_path = ''
as $$
declare s public.subscriptions; v_today date := public.pkt_today(); v_base date; v_new date;
        v_status text; v_start date; v_plan_name text; v_still_valid boolean;
begin
  select pl.name into v_plan_name from public.subscription_plans pl where pl.code = p_plan_code and pl.is_active;
  if v_plan_name is null then
    raise exception 'That plan does not exist or is not active.';
  end if;
  if p_days is null or p_days < 1 or p_days > 3660 then
    raise exception 'Days must be between 1 and 3660.';
  end if;
  select * into s from public.subscriptions where restaurant_id = p_rid for update;
  v_still_valid := found and s.expiry_date >= v_today and s.status in ('trial', 'active');
  v_base := case when v_still_valid then s.expiry_date else v_today end;
  v_new := v_base + p_days;
  v_start := case when v_still_valid then s.start_date else v_today end;
  v_status := case when p_plan_code = 'free_trial' then 'trial' else 'active' end;
  insert into public.subscriptions (restaurant_id, plan_code, status, start_date, expiry_date)
  values (p_rid, p_plan_code, v_status, v_start, v_new)
  on conflict (restaurant_id) do update
    set plan_code = excluded.plan_code, status = excluded.status,
        start_date = excluded.start_date, expiry_date = excluded.expiry_date;
  insert into public.restaurant_messages (restaurant_id, title, body, kind, created_by)
  values (p_rid, 'Subscription updated',
          format('Your %s subscription is active until %s.', v_plan_name, to_char(v_new, 'DD Mon YYYY')),
          'subscription', p_actor);
  return v_new;
end $$;

create or replace function public.admin_overview(p_from date, p_to date) returns jsonb
language plpgsql stable security definer set search_path = ''
as $$
declare v_lo timestamptz; v_hi timestamptz; v_today date := public.pkt_today();
begin
  perform public._require_super();
  if p_from is null or p_to is null or p_from > p_to or p_to - p_from > 366 then
    raise exception 'Please choose a valid date range.';
  end if;
  v_lo := public.pkt_start(p_from);
  v_hi := public.pkt_start(p_to + 1);
  return jsonb_build_object(
    'total_restaurants', (select count(*) from public.restaurants),
    'active_restaurants', (select count(*) from public.restaurants r join public.subscriptions s on s.restaurant_id = r.id
                            where r.status = 'active' and s.status = 'active' and s.expiry_date >= v_today),
    'trial_accounts', (select count(*) from public.restaurants r join public.subscriptions s on s.restaurant_id = r.id
                        where r.status = 'active' and s.status = 'trial' and s.expiry_date >= v_today),
    'expired_subscriptions', (select count(*) from public.subscriptions s
                               where s.status = 'expired' or (s.status in ('trial', 'active') and s.expiry_date < v_today)),
    'suspended_restaurants', (select count(*) from public.restaurants where status = 'suspended'),
    'pending_payments', (select count(*) from public.subscription_payments where status = 'pending'),
    -- The Super Admin sees the platform's OWN income (approved subscription payments), never a
    -- restaurant's orders or sales.
    'payments_approved', (select count(*) from public.subscription_payments p
                           where p.status = 'approved'
                             and coalesce(p.reviewed_at, p.created_at) >= v_lo and coalesce(p.reviewed_at, p.created_at) < v_hi),
    'revenue', coalesce((select sum(p.amount) from public.subscription_payments p
                          where p.status = 'approved'
                            and coalesce(p.reviewed_at, p.created_at) >= v_lo and coalesce(p.reviewed_at, p.created_at) < v_hi), 0),
    'daily_revenue', coalesce((
      select jsonb_agg(jsonb_build_object('date', to_char(x.d, 'YYYY-MM-DD'), 'revenue', x.s) order by x.d)
      from (select (coalesce(p.reviewed_at, p.created_at) at time zone 'Asia/Karachi')::date as d, sum(p.amount) as s
              from public.subscription_payments p
             where p.status = 'approved'
               and coalesce(p.reviewed_at, p.created_at) >= v_lo and coalesce(p.reviewed_at, p.created_at) < v_hi
             group by 1) x), '[]'::jsonb),
    'revenue_by_plan', coalesce((
      select jsonb_agg(jsonb_build_object('plan', x.name, 'payments', x.cnt, 'revenue', x.amt) order by x.amt desc)
      from (select pl.name, count(*) as cnt, sum(p.amount) as amt
              from public.subscription_payments p join public.subscription_plans pl on pl.code = p.plan_code
             where p.status = 'approved'
               and coalesce(p.reviewed_at, p.created_at) >= v_lo and coalesce(p.reviewed_at, p.created_at) < v_hi
             group by pl.code, pl.name) x), '[]'::jsonb)
  );
end $$;

create or replace function public.admin_list_restaurants(p_search text default null) returns jsonb
language plpgsql stable security definer set search_path = ''
as $$
declare v_term text; v_today date := public.pkt_today();
begin
  perform public._require_super();
  v_term := nullif(btrim(coalesce(p_search, '')), '');
  if v_term is not null then
    v_term := '%' || replace(replace(replace(v_term, '\', '\\'), '%', '\%'), '_', '\_') || '%';
  end if;
  return coalesce((
    select jsonb_agg(to_jsonb(x) order by x.name)
    from (select r.id, r.name, r.phone, r.city, r.status, r.created_at,
                 o.email as owner_email, o.full_name as owner_name,
                 s.plan_code, pl.name as plan_name,
                 case when s.status in ('trial', 'active') and s.expiry_date < v_today then 'expired' else s.status end as sub_status,
                 s.start_date, s.expiry_date, (s.expiry_date - v_today) as days_left
            from public.restaurants r
            left join public.profiles o on o.id = r.owner_id
            left join public.subscriptions s on s.restaurant_id = r.id
            left join public.subscription_plans pl on pl.code = s.plan_code
           where v_term is null or r.name ilike v_term or coalesce(r.city, '') ilike v_term
                 or coalesce(o.email, '') ilike v_term) x), '[]'::jsonb);
end $$;

create or replace function public.admin_update_restaurant(p_id uuid, p_name text, p_phone text, p_city text)
returns void
language plpgsql security definer set search_path = ''
as $$
begin
  perform public._require_super();
  if char_length(btrim(coalesce(p_name, ''))) not between 1 and 100 then
    raise exception 'Restaurant name is required (maximum 100 characters).';
  end if;
  update public.restaurants
     set name = btrim(p_name), phone = nullif(left(btrim(coalesce(p_phone, '')), 30), ''),
         city = nullif(left(btrim(coalesce(p_city, '')), 60), '')
   where id = p_id;
  if not found then
    raise exception 'Restaurant not found.';
  end if;
  perform public.log_audit('restaurant.update', 'restaurant', p_id, p_id, jsonb_build_object('name', btrim(p_name)));
end $$;

create or replace function public.admin_set_restaurant_status(p_id uuid, p_status text) returns void
language plpgsql security definer set search_path = ''
as $$
begin
  perform public._require_super();
  if p_status not in ('active', 'suspended') then
    raise exception 'Invalid status.';
  end if;
  update public.restaurants set status = p_status where id = p_id;
  if not found then
    raise exception 'Restaurant not found.';
  end if;
  perform public.log_audit('restaurant.' || p_status, 'restaurant', p_id, p_id);
end $$;

create or replace function public.admin_save_plan(
  p_code text, p_name text, p_price numeric, p_duration_days integer, p_is_activ
