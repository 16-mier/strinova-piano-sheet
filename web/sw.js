// 离线缓存 —— 装到手机上之后**没网也能用**。
//
// ★ 为什么手机版特别需要它 ★
//   桌面版是个本地程序，打开就在。手机版是网页，正常每次都要联网。
//   可用户在琴边上坐着、或者在游戏里想瞄一眼谱子的时候，
//   网断了、或者根本没连 —— 那时候打不开就很扫兴。
//   把静态资源全缓存下来，第二次打开就完全不碰网络了。

const CACHE = 'kaqiu-piano-v1';

// 音源那 16 个 wav 一共 2.4 MB，一次性缓存掉，
// 之后点格子出声不会有任何延迟。
const NOTE_FILES = [
  '1', '2', '3', '4', '5', '6', '7', '8',
  '1_up', '2_up', '3_up', '4_up', '5_up', '6_up', '7_up', '1_up_up',
].map((n) => 'assets/notes/' + n + '.wav');

const ASSETS = [
  './',
  './index.html',
  './manifest.webmanifest',
  './css/app.css',
  './js/app.js',
  './js/parser.js',
  './js/timeline.js',
  './js/layout.js',
  './js/render.js',
  './js/audio.js',
  './js/store.js',
  './icons/icon-192.png',
  './icons/icon-512.png',
  ...NOTE_FILES,
];

self.addEventListener('install', (ev) => {
  ev.waitUntil((async () => {
    const cache = await caches.open(CACHE);
    // 逐个加，别用 addAll —— 它一个失败就整批作废，
    // 而这批里有 16 个 wav，任何一个抽风都不该让离线功能整个没了。
    await Promise.all(ASSETS.map(async (url) => {
      try {
        await cache.add(new Request(url, { cache: 'reload' }));
      } catch (e) {
        console.warn('[sw] 缓存失败：' + url, e);
      }
    }));
    self.skipWaiting();
  })());
});

self.addEventListener('activate', (ev) => {
  ev.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)));
    await self.clients.claim();
  })());
});

self.addEventListener('fetch', (ev) => {
  const req = ev.request;
  if (req.method !== 'GET') return;

  ev.respondWith((async () => {
    // 缓存优先：这是个离线工具，本地那份永远是对的
    const cache = await caches.open(CACHE);
    const hit = await cache.match(req, { ignoreSearch: true });
    if (hit) return hit;
    try {
      const res = await fetch(req);
      // 顺手把新拿到的东西也存下来
      if (res && res.status === 200 && res.type === 'basic') {
        cache.put(req, res.clone()).catch(() => {});
      }
      return res;
    } catch (e) {
      // 彻底没网又没缓存 —— 至少给个能看的页面
      const fallback = await cache.match('./index.html');
      if (fallback) return fallback;
      throw e;
    }
  })());
});
