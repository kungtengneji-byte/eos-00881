/* Service Worker —— 讓手機可離線瀏覽歷史。

   ## 為什麼全部走 network-first

   初版對外殼（HTML/CSS/JS）用 cache-first，結果第一次造訪之後 app.js
   就被永久鎖在快取裡：後續每次改版對使用者都是隱形的，畫面停在舊版，
   而且完全看不出來。cache-first 要正確運作，必須每次部署都手動改 VERSION，
   那是遲早會忘記的一步。

   這個 App 很小（HTML 4KB + JS 26KB + CSS 10KB），network-first 多出來的
   延遲可以忽略，卻換掉一整類「為什麼我看到的是舊版」的問題。
   快取因此退居單純的離線備援。 */

const VERSION = "v3";
const CACHE = `eos-${VERSION}`;

const SHELL = [
  "./",
  "index.html",
  "market.html",
  "app.css",
  "common.js",
  "app.js",
  "market.js",
  "manifest.webmanifest",
  "icon.svg",
];

self.addEventListener("install", (e) => {
  e.waitUntil(
    caches.open(CACHE)
      .then((c) => c.addAll(SHELL))
      .then(() => self.skipWaiting())      // 立刻接手，不等舊的 SW 退場
      .catch(() => self.skipWaiting())     // 單一檔案失敗不該擋住安裝
  );
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(
        keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))
      ))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;   // 不攔截跨網域
  e.respondWith(networkFirst(req));
});

async function networkFirst(req) {
  try {
    const res = await fetch(req, { cache: "no-store" });
    if (res && res.ok) {
      const cache = await caches.open(CACHE);
      cache.put(req, res.clone());
    }
    return res;
  } catch {
    const hit = await caches.match(req);
    if (hit) return hit;
    // 導覽請求離線時退回外殼，至少讓已快取的資料看得到
    if (req.mode === "navigate") {
      const page = new URL(req.url).pathname.split("/").pop() || "index.html";
      const shell = await caches.match(page) || await caches.match("index.html");
      if (shell) return shell;
    }
    return new Response(
      JSON.stringify({ offline: true, message: "離線且無快取" }),
      { status: 503, headers: { "Content-Type": "application/json" } }
    );
  }
}
