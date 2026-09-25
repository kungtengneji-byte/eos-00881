/* Service Worker —— 讓手機可離線瀏覽歷史。

   兩種策略刻意分開：
     外殼（HTML/CSS/JS/圖示）  cache-first  —— 換版才更新，開啟速度優先
     資料（data/*.json）        network-first —— 永遠先要最新分數，
                                              離線時才退回快取

   資料若用 cache-first，手機會顯示過期的 EOS 而看不出來 —— 那比載入慢一秒糟糕得多。 */

const VERSION = "v1";
const SHELL_CACHE = `eos-shell-${VERSION}`;
const DATA_CACHE = `eos-data-${VERSION}`;

const SHELL = [
  "./",
  "index.html",
  "app.css",
  "app.js",
  "manifest.webmanifest",
  "icon.svg",
];

self.addEventListener("install", (e) => {
  e.waitUntil(
    caches.open(SHELL_CACHE)
      .then((c) => c.addAll(SHELL))
      .then(() => self.skipWaiting())
      .catch(() => self.skipWaiting())   // 單一檔案失敗不該擋住安裝
  );
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(
        keys.filter((k) => k !== SHELL_CACHE && k !== DATA_CACHE)
            .map((k) => caches.delete(k))
      ))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET") return;

  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;   // 不攔截跨網域

  if (url.pathname.includes("/data/")) {
    e.respondWith(networkFirst(req));
  } else {
    e.respondWith(cacheFirst(req));
  }
});

async function networkFirst(req) {
  try {
    const res = await fetch(req);
    if (res && res.ok) {
      const cache = await caches.open(DATA_CACHE);
      cache.put(req, res.clone());
    }
    return res;
  } catch {
    const hit = await caches.match(req);
    if (hit) return hit;
    return new Response(JSON.stringify({ offline: true }), {
      status: 503, headers: { "Content-Type": "application/json" },
    });
  }
}

async function cacheFirst(req) {
  const hit = await caches.match(req);
  if (hit) return hit;
  try {
    const res = await fetch(req);
    if (res && res.ok) {
      const cache = await caches.open(SHELL_CACHE);
      cache.put(req, res.clone());
    }
    return res;
  } catch {
    const shell = await caches.match("index.html");
    if (shell) return shell;
    throw new Error("offline and no cache");
  }
}
