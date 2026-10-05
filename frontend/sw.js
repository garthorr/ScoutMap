/* ScoutMap offline helper.
 *
 * Network first: when there's a signal everything comes fresh from the server.
 * When there isn't, pages, scripts and data come from the last good copy, so an
 * adult entering visits can keep going (and a phone that reloads the tab mid-walk
 * still opens). Unsent visits themselves are kept by entry.js, not here.
 */
const CACHE = "scoutmap-v1";

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));

self.addEventListener("fetch", (event) => {
  const req = event.request;
  const url = new URL(req.url);
  if (req.method !== "GET" || url.origin !== self.location.origin) return;
  if (url.pathname.startsWith("/api/auth/")) return;  // never keep sign-in responses

  event.respondWith(
    fetch(req)
      .then((res) => {
        if (res.ok) {
          const copy = res.clone();
          caches.open(CACHE).then((cache) => cache.put(req, copy));
        }
        return res;
      })
      .catch(() => caches.match(req).then((hit) => hit || Response.error()))
  );
});
