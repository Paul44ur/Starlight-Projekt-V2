// Starlight's service worker: keeps the chat page's shell on the phone, so that when the PC
// is off or asleep the page still opens and says so (with "Wake the PC" when that is set
// up) instead of the browser's error page. Everything else always goes to the PC: answers,
// conversations and settings are never kept here.
const SHELL = "starlight-shell-v5";
const FILES = ["/", "/style.css", "/common.js", "/quick.js", "/i18n.js", "/markdown.js", "/render.js", "/chat.css", "/chat-core.js", "/chat-messages.js", "/chat-list.js", "/chat-input.js", "/chat-page.js", "/icon.svg", "/kill"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(SHELL).then((cache) => cache.addAll(FILES)).catch(() => {}));
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((names) => Promise.all(names.filter((n) => n !== SHELL).map((n) => caches.delete(n))))
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET" || url.origin !== location.origin) return;
  const shell = FILES.includes(url.pathname);
  if (!shell) return;  // the API and every other page: straight to the PC, never cached
  // The PC first (a fresh page), the kept copy only when it does not answer.
  event.respondWith(
    fetch(event.request)
      .then((response) => {
        if (response.ok && !response.redirected) {  // (not the pairing page instead)
          const copy = response.clone();
          caches.open(SHELL).then((cache) => cache.put(event.request, copy));
        }
        return response;
      })
      .catch(() => caches.match(event.request).then((kept) => kept || Response.error()))
  );
});
