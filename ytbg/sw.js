// Service worker cũ tự gỡ: xoá bộ nhớ đệm rồi huỷ đăng ký, để trình duyệt tải trang thông báo mới.
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys()
      .then((ks) => Promise.all(ks.map((k) => caches.delete(k))))
      .then(() => self.registration.unregister())
      .then(() => self.clients.matchAll({ type: 'window' }))
      .then((cs) => cs.forEach((c) => c.navigate(c.url)))
  );
});
