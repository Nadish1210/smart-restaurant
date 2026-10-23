/**
 * Edge Function: admin-users
 *
 * Creates login accounts (Supabase Auth users) for the dashboard. This is the
 * ONLY place that uses the service-role key, and the key never leaves
 * Supabase's servers.
 *
 * Who may call it (checked here AND again inside the database functions):
 *   create_restaurant, assign_owner -> Super Admin only
 *   create_employee                 -> the restaurant Owner only
 *
 * Deploy with JWT verification OFF (see README). The function verifies the
 * caller's token itself, which works with every kind of Supabase API key.
 */
import { createClient } from "npm:@supabase/supabase-js@2";

// <logic>
const CORS: Record<string, string> = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, apikey, content-type, x-client-info",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
};

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { ...CORS, "Content-Type": "application/json" } });
}

class UserError extends Error {
  status: number;
  constructor(message: string, status = 400) {
    super(message);
    this.status = status;
  }
}

const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const CODE_RE = /^[a-z0-9_]{2,40}$/;

function text(v: unknown, label: string, max: number, required = false): string {
  const s = String(v ?? "").replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f]/g, "").trim();
  if (required && !s) throw new UserError(`${label} is required.`);
  if (s.length > max) throw new UserError(`${label} must be at most ${max} characters.`);
  return s;
}

function email(v: unknown): string {
  const s = text(v, "Email", 254, true).toLowerCase();
  if (!EMAIL_RE.test(s)) throw new UserError("Please enter a valid email address.");
  return s;
}

function password(v: unknown): string {
  const s = String(v ?? "");
  if (s.length < 8) throw new UserError("Password must be at least 8 characters.");
  if (s.length > 128) throw new UserError("Password must be at most 128 characters.");
  if (!/[A-Za-z]/.test(s) || !/[0-9]/.test(s)) throw new UserError("Password must contain at least one letter and one number.");
  return s;
}

function uuid(v: unknown, label: string): string {
  const s = String(v ?? "");
  if (!UUID_RE.test(s)) throw new UserError(`${label} is not valid.`);
  return s;
}

function percent(v: unknown): number {
  const n = Number(v);
  if (!Number.isFinite(n) || n < 0 || n > 100) throw new UserError("Commission must be between 0 and 100.");
  return Math.round(n * 100) / 100;
}

// Database errors -> a safe message for the user.
function dbError(error: { code?: string; message?: string }): UserError {
  if (error.code === "42501") return new UserError("You do not have permission to do that.", 403);
  if (error.code === "P0001") return new UserError(String(error.message ?? "The request could not be completed.").slice(0, 300), 400);
  if (error.code === "23505") return new UserError("That email or name is already in use.", 409);
  console.error("admin-users database error", error.code);
  return new UserError("The request could not be completed. Please try again.", 500);
}

function authError(error: { message?: string } | null): UserError {
  const m = String(error?.message ?? "").toLowerCase();
  if (m.includes("already") || m.includes("registered") || m.includes("exists")) return new UserError("That email is already registered.", 409);
  if (m.includes("password")) return new UserError("That password is not accepted. Please choose a stronger one.", 400);
  console.error("admin-users auth error");
  return new UserError("The account could not be created. Please try again.", 500);
}

// deno-lint-ignore no-explicit-any
type Admin = any;

async function findUser(admin: Admin, mail: string): Promise<string | null> {
  const { data, error } = await admin.rpc("service_find_user_by_email", { p_email: mail });
  if (error) throw dbError(error);
  return data ?? null;
}

async function createUser(admin: Admin, mail: string, pass: string, fullName: string): Promise<string> {
  // The role is NOT stored in user metadata (users can edit their own metadata).
  const { data, error } = await admin.auth.admin.createUser({
    email: mail, password: pass, email_confirm: true, user_metadata: { full_name: fullName },
  });
  if (error || !data?.user) throw authError(error);
  return data.user.id as string;
}

async function undoUser(admin: Admin, userId: string | null) {
  if (!userId) return;
  try {
    await admin.auth.admin.deleteUser(userId);
  } catch (_e) {
    console.error("admin-users: could not remove the half-created account");
  }
}

export async function handle(req: Request, admin: Admin): Promise<Response> {
  if (req.method === "OPTIONS") return new Response("ok", { headers: CORS });
  try {
    if (req.method !== "POST") throw new UserError("Method not allowed.", 405);

    // 1. Who is calling? (verified by Supabase Auth, never taken from the request body)
    const token = (req.headers.get("Authorization") ?? "").replace(/^Bearer\s+/i, "").trim();
    if (!token) throw new UserError("Please sign in again.", 401);
    const { data: who, error: whoError } = await admin.auth.getUser(token);
    if (whoError || !who?.user) throw new UserError("Your session expired. Please sign in again.", 401);
    const actor: string = who.user.id;

    // 2. What are they allowed to do? (role comes from our own table)
    const { data: profile, error: profError } = await admin.from("profiles")
      .select("role, is_active, restaurant_id").eq("id", actor).maybeSingle();
    if (profError) throw dbError(profError);
    if (!profile || !profile.is_active) throw new UserError("You do not have permission to do that.", 403);

    let body: Record<string, unknown>;
    try {
      body = await req.json();
    } catch (_e) {
      throw new UserError("Invalid request.");
    }

    const action = String(body.action ?? "");

    if (action === "create_restaurant") {
      if (profile.role !== "super_admin") throw new UserError("You do not have permission to do that.", 403);
      const name = text(body.name, "Restaurant name", 100, true);
      const phone = text(body.phone, "Phone", 30);
      const city = text(body.city, "City", 60);
      const ownerName = text(body.owner_name, "Owner name", 100, true);
      const ownerEmail = email(body.owner_email);
      const ownerPassword = password(body.owner_password);
      const planCode = String(body.plan_code ?? "");
      if (!CODE_RE.test(planCode)) throw new UserError("Plan is not valid.");
      if (await findUser(admin, ownerEmail)) throw new UserError("That owner email is already registered. Use a different email, or use “Assign owner” on an existing restaurant.", 409);

      const userId = await createUser(admin, ownerEmail, ownerPassword, ownerName);
      const { data, error } = await admin.rpc("service_create_restaurant", {
        p_actor: actor, p_name: name, p_phone: phone, p_city: city, p_owner_id: userId,
        p_owner_email: ownerEmail, p_owner_name: ownerName, p_plan_code: planCode,
      });
      if (error) {
        await undoUser(admin, userId);
        throw dbError(error);
      }
      return json({ ok: true, ...data });
    }

    if (action === "assign_owner") {
      if (profile.role !== "super_admin") throw new UserError("You do not have permission to do that.", 403);
      const restaurantId = uuid(body.restaurant_id, "Restaurant");
      const ownerEmail = email(body.email);
      const fullName = text(body.full_name, "Owner name", 100);
      let userId = await findUser(admin, ownerEmail);
      let created: string | null = null;
      if (!userId) {
        if (!body.password) throw new UserError("Enter a temporary password to create the new owner account.");
        userId = created = await createUser(admin, ownerEmail, password(body.password), fullName);
      }
      const { data, error } = await admin.rpc("service_assign_owner", {
        p_actor: actor, p_restaurant_id: restaurantId, p_user_id: userId, p_email: ownerEmail, p_name: fullName,
      });
      if (error) {
        await undoUser(admin, created);
        throw dbError(error);
      }
      return json({ ok: true, ...data });
    }

    if (action === "create_employee") {
      if (profile.role !== "owner") throw new UserError("You do not have permission to do that.", 403);
      const mail = email(body.email);
      const fullName = text(body.full_name, "Waiter name", 100, true);
      const pass = password(body.password);
      const phone = text(body.phone, "Phone", 30);
      const pct = percent(body.commission_pct ?? 0);
      if (await findUser(admin, mail)) throw new UserError("That email is already registered. Use a different email.", 409);

      const userId = await createUser(admin, mail, pass, fullName);
      const { data, error } = await admin.rpc("service_create_employee", {
        p_actor: actor, p_user_id: userId, p_email: mail, p_name: fullName, p_phone: phone, p_pct: pct,
      });
      if (error) {
        await undoUser(admin, userId);
        throw dbError(error);
      }
      return json({ ok: true, ...data });
    }

    throw new UserError("Unknown action.");
  } catch (e) {
    if (e instanceof UserError) return json({ error: e.message }, e.status);
    console.error("admin-users unexpected error:", (e as Error)?.message);
    return json({ error: "Something went wrong. Please try again." }, 500);
  }
}
// </logic>

Deno.serve((req: Request) => {
  const url = Deno.env.get("SUPABASE_URL");
  const serviceKey = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY");
  if (!url || !serviceKey) {
    console.error("admin-users: the Supabase environment variables are missing");
    return Promise.resolve(json({ error: "The server is not configured correctly." }, 500));
  }
  const admin = createClient(url, serviceKey, { auth: { persistSession: false, autoRefreshToken: false } });
  return handle(req, admin);
});
