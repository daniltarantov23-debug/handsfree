/*
 * Оболочка приложения в офлайне: страница и данные уроков.
 *
 * Аудио здесь НЕ кешируется намеренно. iOS требует от медиа частичных ответов
 * (Range), а воркер отдаёт цельный ответ - перемотка ломается, а на 74-минутном
 * файле это половина смысла. Поэтому mp3 лежит в отдельном хранилище
 * (handsfree-audio-*) и играет через blob-ссылку: она перематывается сама.
 */
var SHELL = 'handsfree-shell-v1';
var FILES = ['./', './index.html', './data.js'];

self.addEventListener('install', function(e){
  e.waitUntil(caches.open(SHELL).then(function(c){ return c.addAll(FILES); })
    .then(function(){ return self.skipWaiting(); }));
});

self.addEventListener('activate', function(e){
  e.waitUntil(caches.keys().then(function(keys){
    return Promise.all(keys.filter(function(k){
      // Аудио переживает смену версии оболочки: оно скачано человеком вручную,
      // сносить его при обновлении приложения нельзя.
      return k !== SHELL && k.indexOf('handsfree-audio') !== 0;
    }).map(function(k){ return caches.delete(k); }));
  }).then(function(){ return self.clients.claim(); }));
});

self.addEventListener('fetch', function(e){
  var req = e.request;
  if(req.method !== 'GET') return;
  var url = new URL(req.url);
  if(url.origin !== location.origin) return;
  if(/\.(mp3|m4a|wav)$/i.test(url.pathname)) return;   // аудио - мимо воркера

  // Сначала сеть, чтобы правки приложения доезжали сразу; без сети - из кеша.
  e.respondWith(fetch(req).then(function(res){
    var copy = res.clone();
    caches.open(SHELL).then(function(c){ c.put(req, copy); }).catch(function(){});
    return res;
  }).catch(function(){
    return caches.match(req).then(function(r){ return r || caches.match('./index.html'); });
  }));
});
