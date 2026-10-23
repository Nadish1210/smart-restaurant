/*
 * Customer ordering page - settings.
 * Edit the two values below. BOTH are safe to publish: the "anon" key is a
 * public key (Supabase -> Project Settings -> API). NEVER put the service_role
 * or "secret" key here.
 */
window.SMART_RESTAURANT_CONFIG = {
  SUPABASE_URL: "https://vfhbgolnzbzqsnjovvku.supabase.co",
  SUPABASE_ANON_KEY: "sb_publishable_iLac-2-_2fWmFHob5TVrWw_976f3KjY",
  // Set to true only after deploying the "ai-assistant" Edge Function and
  // switching AI on in the dashboard (Super Admin -> AI Assistant).
  ENABLE_AI: false
};
