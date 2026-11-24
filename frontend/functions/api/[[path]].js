// Cloudflare Pages Function: proxy /api/* to the API on Render, so the dashboard and the API share
// one origin. Lens sessions use SameSite=Lax cookies + a CSRF header (design §18.2); browsers do not
// send those cookies on cross-site requests from *.pages.dev to *.onrender.com, so without this
// proxy, sign-in would not work on the split free-tier deployment.
//
// Set API_ORIGIN in the Pages project (Settings → Environment variables), e.g.
//   API_ORIGIN = https://rideops-api.onrender.com
// and leave VITE_API_BASE_URL empty so the app calls /api/v1 on its own origin.
export async function onRequest({ request, env }) {
  if (!env.API_ORIGIN) {
    return new Response(JSON.stringify({ error: { code: "NOT_CONFIGURED", message: "API_ORIGIN is not set" } }), {
      status: 502,
      headers: { "Content-Type": "application/json" },
    });
  }
  const url = new URL(request.url);
  const target = new URL(url.pathname + url.search, env.API_ORIGIN);
  const headers = new Headers(request.headers);
  headers.set("X-Forwarded-For", request.headers.get("CF-Connecting-IP") || "");
  headers.set("X-Forwarded-Proto", "https");
  headers.delete("Host");
  const hasBody = !["GET", "HEAD"].includes(request.method);
  return fetch(target.toString(), {
    method: request.method,
    headers,
    body: hasBody ? request.body : undefined,
    redirect: "manual",
  });
}
