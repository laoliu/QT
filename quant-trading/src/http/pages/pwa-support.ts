/**
 * PWA Support (UI-11) — Service Worker + Web App Manifest
 *
 * Provides offline capability and installable web app experience
 */

/** Web App Manifest JSON */
export function getManifestJson(baseUrl: string): string {
  const b = baseUrl.replace(/\/$/, "");
  return JSON.stringify(
    {
      name: "OpenClaw 量化交易",
      short_name: "OpenClaw",
      description: "A-share quantitative trading platform",
      start_url: `${b}/quant/`,
      display: "standalone",
      background_color: "#0d1117",
      theme_color: "#0d1117",
      icons: [
        {
          src: `${b}/quant/pwa/icon-192.svg`,
          sizes: "192x192",
          type: "image/svg+xml",
        },
        {
          src: `${b}/quant/pwa/icon-512.svg`,
          sizes: "512x512",
          type: "image/svg+xml",
        },
      ],
    },
    null,
    2,
  );
}

/** SVG icon for PWA */
export function getPwaIconSvg(): string {
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">
  <rect width="512" height="512" rx="80" fill="#0d1117"/>
  <text x="256" y="340" font-size="280" text-anchor="middle" fill="#58a6ff" font-family="sans-serif">02</text>
</svg>`;
}

/** Service Worker script */
export function getServiceWorkerJs(baseUrl: string): string {
  const b = baseUrl.replace(/\/$/, "");
  return `// OpenClaw Service Worker — offline caching for PWA
const CACHE_NAME = 'openclaw-v1';
const SHELL_URLS = [
  '${b}/quant/',
  '${b}/quant/dashboard/ui',
  '${b}/quant/admin/ui',
];

self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => cache.addAll(SHELL_URLS))
  );
  self.skipWaiting();
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);

  // API requests: network-first
  if (url.pathname.includes('/api/')) {
    event.respondWith(
      fetch(event.request).catch(() => caches.match(event.request))
    );
    return;
  }

  // Pages + assets: stale-while-revalidate
  event.respondWith(
    caches.match(event.request).then(cached => {
      const network = fetch(event.request).then(response => {
        if (response.ok) {
          const clone = response.clone();
          caches.open(CACHE_NAME).then(cache => cache.put(event.request, clone));
        }
        return response;
      }).catch(() => cached);
      return cached || network;
    })
  );
});
`;
}

/**
 * PWA registration snippet to inject into HTML pages.
 * Call this and insert into <head> of pages that should be installable.
 */
export function getPwaHeadSnippet(baseUrl: string): string {
  const b = baseUrl.replace(/\/$/, "");
  return `<link rel="manifest" href="${b}/quant/pwa/manifest.json">
<meta name="theme-color" content="#0d1117">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<script>if('serviceWorker' in navigator){navigator.serviceWorker.register('${b}/quant/pwa/sw.js').catch(()=>{})}</script>`;
}
