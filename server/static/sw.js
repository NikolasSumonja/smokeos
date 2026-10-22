// Minimal service worker so SmokeOS can be installed as a home-screen app.
// It never caches dashboard data - everything is always fetched live.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));
self.addEventListener("fetch", () => {});
