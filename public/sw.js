// Service worker minimal pour rendre l'app installable (exigé par PWABuilder
// et les navigateurs pour le "Add to Home Screen").
//
// Volontairement SANS mise en cache des pages : c'est une application
// financière — mieux vaut qu'elle échoue proprement hors ligne plutôt que
// d'afficher un solde ou un historique périmé depuis le cache.

self.addEventListener("install", () => {
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});

// Laisse passer toutes les requêtes vers le réseau normalement.
self.addEventListener("fetch", () => {
  // no-op : pas d'interception, pas de cache.
});
