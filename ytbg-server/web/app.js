'use strict';
(() => {
  // ================================================================ tiện ích
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const el = (html) => { const t = document.createElement('template'); t.innerHTML = html.trim(); return t.content.firstElementChild; };
  const LS = {
    get(k, d) { try { const v = localStorage.getItem('ytbg2:' + k); return v == null ? d : JSON.parse(v); } catch { return d; } },
    set(k, v) { try { localStorage.setItem('ytbg2:' + k, JSON.stringify(v)); } catch {} },
    del(k) { try { localStorage.removeItem('ytbg2:' + k); } catch {} },
  };
  const ytThumb = (id) => `https://i.ytimg.com/vi/${id}/mqdefault.jpg`;
  const thumbOf = (id) => localThumbs.get(id) || ytThumb(id);
  const slim = (v) => ({ id: v.id, title: v.title || '', channel: v.channel || '', channelId: v.channelId || '', duration: v.duration || null });

  function fmtDur(s) {
    if (s == null || isNaN(s)) return '';
    s = Math.max(0, Math.floor(s));
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), ss = String(s % 60).padStart(2, '0');
    return h ? `${h}:${String(m).padStart(2, '0')}:${ss}` : `${m}:${ss}`;
  }
  function fmtViews(n) {
    if (n == null) return '';
    const f = (x) => x.toFixed(x < 10 ? 1 : 0).replace('.0', '').replace('.', ',');
    if (n >= 1e9) return f(n / 1e9) + ' T lượt xem';
    if (n >= 1e6) return f(n / 1e6) + ' Tr lượt xem';
    if (n >= 1e3) return f(n / 1e3) + ' N lượt xem';
    return n + ' lượt xem';
  }
  const fmtDate = (d) => (d && d.length === 8 ? `${d.slice(6)}/${d.slice(4, 6)}/${d.slice(0, 4)}` : '');
  const fmtMB = (b) => (b / 1048576).toFixed(b < 10485760 ? 1 : 0) + ' MB';
  const subLine = (v) => [v.channel, fmtViews(v.views)].filter(Boolean).join(' · ');
  const durBadge = (v) => (v.live ? '<span class="dur live">TRỰC TIẾP</span>' : v.duration ? `<span class="dur">${fmtDur(v.duration)}</span>` : '');

  let toastTimer;
  function toast(msg, ms = 2600) {
    const t = $('#toast');
    t.textContent = msg; t.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { t.hidden = true; }, ms);
  }
  const guard = (fn) => async (...a) => { try { return await fn(...a); } catch (e) { if (e.status !== 401) toast('⚠ ' + (e.message || e)); } };

  function parseYouTubeId(s) {
    s = s.trim();
    if (/^[\w-]{11}$/.test(s)) return s;
    try {
      const u = new URL(s.startsWith('http') ? s : 'https://' + s);
      if (!/youtu\.?be/.test(u.hostname)) return null;
      const v = u.searchParams.get('v');
      if (v && /^[\w-]{11}$/.test(v)) return v;
      const m = u.pathname.match(/\/(?:shorts|embed|live|v)\/([\w-]{11})/) || (u.hostname.includes('youtu.be') && u.pathname.match(/^\/([\w-]{11})/));
      return m ? m[1] : null;
    } catch { return null; }
  }

  // ================================================================ API
  async function api(path, { method = 'GET', body, timeout = 45000 } = {}) {
    const ctl = new AbortController();
    const t = setTimeout(() => ctl.abort(), timeout);
    let r;
    try {
      r = await fetch('/api' + path, {
        method, signal: ctl.signal, credentials: 'same-origin',
        headers: body ? { 'Content-Type': 'application/json', 'X-YTBG': '1' } : { 'X-YTBG': '1' },
        body: body ? JSON.stringify(body) : undefined,
      });
    } catch (e) {
      throw Object.assign(new Error(e.name === 'AbortError' ? 'Máy chủ phản hồi quá lâu' : 'Không kết nối được máy chủ'), { offline: true });
    } finally { clearTimeout(t); }
    const j = await r.json().catch(() => ({}));
    if (r.status === 401 && path !== '/login') { sessionExpired(); }
    if (!r.ok) throw Object.assign(new Error(j.error || 'Lỗi ' + r.status), { status: r.status });
    return j;
  }

  // ================================================================ đăng nhập
  let me = LS.get('me', null);
  let started = false;

  function showLogin(msg) {
    $('#app').hidden = true;
    $('#login').hidden = false;
    $('#lErr').textContent = msg || '';
    setTimeout(() => $('#lUser').focus(), 50);
  }
  function sessionExpired() {
    if ($('#login').hidden === false) return;
    me = null; LS.del('me');
    audio.pause(); userWantsPlay = false;
    showLogin('Phiên đăng nhập đã hết — vui lòng đăng nhập lại.');
  }
  $('#loginForm').onsubmit = async (e) => {
    e.preventDefault();
    $('#lBtn').disabled = true; $('#lErr').textContent = '';
    try {
      const m = await api('/login', { method: 'POST', body: { username: $('#lUser').value, password: $('#lPass').value } });
      if (LS.get('lastUser', null) !== m.user) { LS.del('playlists'); LS.del('queue'); LS.del('pos'); }
      LS.set('lastUser', m.user);
      me = m; LS.set('me', m);
      $('#lPass').value = '';
      if (started) location.reload(); else start();
    } catch (err) { $('#lErr').textContent = err.message; }
    finally { $('#lBtn').disabled = false; }
  };
  async function logout() {
    if (!confirm('Đăng xuất khỏi thiết bị này?')) return;
    try { await api('/logout', { method: 'POST', body: {} }); } catch {}
    audio.pause();
    LS.del('me'); LS.del('playlists'); LS.del('queue'); LS.del('pos');
    location.hash = '#/'; location.reload();
  }

  async function boot() {
    try {
      me = await api('/me');
      LS.set('me', me); LS.set('lastUser', me.user);
      start();
    } catch (e) {
      if (e.status === 401) showLogin();
      else if (me) { start(); toast('Không kết nối được máy chủ — chỉ nghe được bài đã tải.', 4000); }
      else showLogin('Không kết nối được máy chủ. Kiểm tra mạng rồi tải lại trang.');
    }
  }
  function start() {
    $('#login').hidden = true;
    $('#app').hidden = false;
    $('#tabAdmin').hidden = !me.admin;
    if (!started) {
      started = true;
      initOffline();
      syncPlaylists();
      restoreQueue();
    }
    route();
  }

  // ================================================================ điều hướng
  const videoCache = new Map(); // id -> thông tin video đã biết (để hiện ngay khi mở trang xem)
  const remember = (v) => { if (v && v.id) videoCache.set(v.id, { ...(videoCache.get(v.id) || {}), ...v }); return v; };
  let routeToken = 0, cleanup = null, curRoute = { name: '' };

  window.addEventListener('hashchange', () => route());
  function parseHash() {
    const [path, qs] = (location.hash.slice(1) || '/').split('?');
    const parts = path.split('/').filter(Boolean).map(decodeURIComponent);
    return { parts, q: new URLSearchParams(qs || '') };
  }
  function route(soft) {
    if (!me) return;
    const { parts, q } = parseHash();
    const name = parts[0] || 'home';
    const y = window.scrollY;
    if (cleanup) { try { cleanup(); } catch {} cleanup = null; }
    const token = ++routeToken;
    curRoute = { name, parts, q };
    const tab = { home: 'home', watch: 'home', channel: 'home', search: 'search', library: 'library', playlist: 'library', history: 'library', admin: 'admin' }[name] || 'home';
    $$('.tabs a').forEach((a) => a.classList.toggle('on', a.dataset.tab === tab));
    const view = $('#view');
    view.innerHTML = '';
    const alive = () => token === routeToken;
    const pages = { home: pageHome, search: pageSearch, watch: pageWatch, channel: pageChannel, library: pageLibrary, playlist: pagePlaylist, history: pageHistory, admin: pageAdmin };
    (pages[name] || pageHome)(view, { parts, q, alive });
    if (soft) window.scrollTo(0, y); else window.scrollTo(0, 0);
  }
  const rerenderIf = (...names) => { if (names.includes(curRoute.name)) route(true); };

  // ---------- thẻ video ----------
  function vcard(v) {
    remember(v);
    const d = el(`<div class="vcard">
      <a class="thumb" href="#/watch/${v.id}"><img loading="lazy" alt="" src="${esc(thumbOf(v.id))}">${durBadge(v)}</a>
      <div class="vmeta"><a class="txt" href="#/watch/${v.id}"><div class="vtitle">${esc(v.title)}</div><div class="vsub">${esc(subLine(v))}</div></a>
      <button class="more" aria-label="Tuỳ chọn">⋮</button></div></div>`);
    $('.more', d).onclick = () => videoMenu(v);
    return d;
  }
  function lrow(v, { onClick, right, cur, href } = {}) {
    remember(v);
    const d = el(`<div class="lrow${cur ? ' cur' : ''}">
      <a class="thumb"><img loading="lazy" alt="" src="${esc(thumbOf(v.id))}">${durBadge(v)}</a>
      <a class="txt"><div class="vtitle">${esc(v.title || v.id)}</div><div class="vsub">${esc(subLine(v))}</div></a></div>`);
    const go = href || `#/watch/${v.id}`;
    $$('a', d).forEach((a) => {
      if (onClick) a.onclick = (e) => { e.preventDefault(); onClick(); };
      else a.href = go;
    });
    if (right) d.append(right);
    else { const m = el('<button class="more" aria-label="Tuỳ chọn">⋮</button>'); m.onclick = () => videoMenu(v); d.append(m); }
    return d;
  }
  const loadingEl = (t = 'Đang tải…') => el(`<div class="loading">${esc(t)}</div>`);
  const emptyEl = (t) => el(`<div class="empty">${t}</div>`);

  // ---------- bảng chọn ----------
  function openSheet(build) {
    const body = $('#sheetBody');
    body.innerHTML = '';
    build(body);
    $('#sheet').hidden = false;
  }
  const closeSheet = () => { $('#sheet').hidden = true; };
  $('#sheet').onclick = (e) => { if (e.target.id === 'sheet') closeSheet(); };
  function sheetItem(icon, label, fn) {
    const b = el(`<button class="sheet-item"><span class="ic">${icon}</span><span class="ell"></span></button>`);
    b.lastChild.textContent = label;
    b.onclick = () => { closeSheet(); fn(); };
    return b;
  }
  function videoMenu(v) {
    openSheet((b) => {
      const h = el('<h3 class="ell"></h3>'); h.textContent = v.title || v.id; b.append(h);
      b.append(sheetItem('🎧', 'Nghe nền', () => playQueue([v], 0, { from: 'Nghe nền' })));
      b.append(sheetItem('⏭', 'Phát tiếp theo', () => playNext(v)));
      b.append(sheetItem('＋', 'Lưu vào playlist', () => addSheet(v)));
      b.append(sheetItem('▶', 'Xem video', () => { location.hash = '#/watch/' + v.id; }));
      if (v.channelId) b.append(sheetItem('📺', 'Mở kênh ' + (v.channel || ''), () => { location.hash = '#/channel/' + encodeURIComponent(v.channelId); }));
    });
  }
  function addSheet(v) {
    openSheet((b) => {
      b.append(el('<h3>Lưu vào playlist</h3>'));
      if (!navigator.onLine) b.append(el('<div class="empty">Đang offline — cần mạng để sửa playlist.</div>'));
      for (const pl of playlists) {
        const has = pl.items.some((i) => i.id === v.id);
        const it = el(`<button class="sheet-item"><span class="ic">${has ? '☑' : '☐'}</span><span class="ell"></span><span class="muted" style="margin-left:auto;font-size:13px">${pl.items.length}</span></button>`);
        it.children[1].textContent = pl.name;
        it.onclick = () => { closeSheet(); has ? removeFromPlaylist(pl.id, v.id) : addToPlaylist(pl.id, v); };
        b.append(it);
      }
      b.append(sheetItem('＋', 'Tạo playlist mới', () => {
        const name = prompt('Tên playlist mới:');
        if (name && name.trim()) createPlaylist(name.trim(), v);
      }));
    });
  }

  // ================================================================ Phần 1 — XEM
  // ---------- trang chủ ----------
  async function pageHome(view, { alive }) {
    const chips = el('<div class="chips"></div>');
    const body = el('<div></div>');
    view.append(chips, body);
    if (!navigator.onLine) {
      body.append(emptyEl('Đang offline. Vào <a href="#/library" style="color:var(--accent)">Thư viện</a> để nghe các bài đã tải về máy.'));
      return;
    }
    let data = null;
    try { const c = JSON.parse(sessionStorage.getItem('ytbg2:home') || 'null'); if (c && Date.now() - c.t < 10 * 60e3) data = c.data; } catch {}
    if (!data) {
      body.append(loadingEl('Đang tải trang chủ… (lần đầu có thể mất vài giây)'));
      try { data = await api('/home', { timeout: 120000 }); }
      catch (e) { if (!alive()) return; body.innerHTML = ''; body.append(emptyEl('⚠ ' + esc(e.message))); return; }
      try { sessionStorage.setItem('ytbg2:home', JSON.stringify({ t: Date.now(), data })); } catch {}
    }
    if (!alive()) return;
    body.innerHTML = '';
    for (const t of data.topics) {
      const c = el('<a class="chip"></a>'); c.textContent = t; c.href = '#/search?q=' + encodeURIComponent(t); chips.append(c);
    }
    const edit = el('<button class="chip">✎ Chủ đề</button>');
    edit.onclick = guard(async () => {
      const s = prompt('Các chủ đề trên trang chủ (cách nhau bằng dấu phẩy):', data.topics.join(', '));
      if (s == null) return;
      await api('/topics', { method: 'PUT', body: { topics: s.split(',').map((x) => x.trim()).filter(Boolean) } });
      sessionStorage.removeItem('ytbg2:home');
      route();
    });
    chips.append(edit);
    if (!data.sections.length) body.append(emptyEl('Chưa có gì để hiện. Thêm chủ đề hoặc tìm kiếm để bắt đầu.'));
    for (const s of data.sections) {
      const h = el(`<h2 class="sec"><span>${esc(s.title)}</span></h2>`);
      if (s.query) h.append(el(`<a href="#/search?q=${encodeURIComponent(s.query)}">Xem thêm</a>`));
      if (s.channelId) h.append(el(`<a href="#/channel/${encodeURIComponent(s.channelId)}">Kênh</a>`));
      if (s.kind === 'history') h.append(el('<a href="#/history">Tất cả</a>'));
      const row = el('<div class="hscroll"></div>');
      s.items.forEach((v) => row.append(vcard(v)));
      body.append(h, row);
    }
    const r = el('<div class="pad" style="margin-top:16px"><button class="btn sm">↻ Làm mới</button></div>');
    $('button', r).onclick = () => { sessionStorage.removeItem('ytbg2:home'); route(); };
    body.append(r);
  }

  // ---------- tìm kiếm ----------
  const searchCache = new Map();
  function pageSearch(view, { q, alive }) {
    const query = (q.get('q') || '').trim();
    const bar = el(`<form class="searchbar"><input type="search" enterkeyhint="search" placeholder="Tìm trên YouTube hoặc dán link…" autocomplete="off"><button class="btn primary">Tìm</button></form>`);
    const input = $('input', bar);
    input.value = query;
    bar.onsubmit = (e) => {
      e.preventDefault();
      const s = input.value.trim();
      if (!s) return;
      const id = parseYouTubeId(s);
      location.hash = id ? '#/watch/' + id : '#/search?q=' + encodeURIComponent(s);
    };
    const list = el('<div class="list"></div>');
    view.append(bar, list);
    if (!query) { setTimeout(() => input.focus(), 50); list.append(emptyEl('Gõ tên bài hát, ca sĩ hoặc dán link YouTube.')); return; }
    input.blur();
    let st = searchCache.get(query);
    const more = el('<div class="pad" style="margin:12px 0"><button class="btn" style="width:100%;justify-content:center">Xem thêm</button></div>');
    const draw = () => {
      list.innerHTML = '';
      if (!st.items.length) list.append(emptyEl('Không có kết quả.'));
      st.items.forEach((v) => list.append(lrow(v)));
      more.hidden = !st.next;
    };
    const load = async (page) => {
      const j = await api(`/search?q=${encodeURIComponent(query)}&page=${page}`, { timeout: 90000 });
      if (!st) st = { items: [], next: null };
      const seen = new Set(st.items.map((x) => x.id));
      st.items.push(...j.items.filter((x) => !seen.has(x.id)));
      st.next = j.nextpage;
      searchCache.set(query, st);
    };
    view.append(more);
    more.hidden = true;
    $('button', more).onclick = guard(async () => {
      $('button', more).disabled = true; $('button', more).textContent = 'Đang tải…';
      try { await load(st.next); if (alive()) draw(); }
      finally { $('button', more).disabled = false; $('button', more).textContent = 'Xem thêm'; }
    });
    if (st) { draw(); return; }
    list.append(loadingEl('Đang tìm…'));
    load(1).then(() => { if (alive()) draw(); }).catch((e) => { if (alive()) { list.innerHTML = ''; list.append(emptyEl('⚠ ' + esc(e.message))); } });
  }

  // ---------- xem video ----------
  // Trình phát nằm trong #vdock (ngoài #view) để video đang ở "cửa sổ nổi" không bị dừng khi chuyển trang.
  const vdock = $('#vdock'), videoEl = $('#vid'), ytbox = $('#ytbox');
  let V = {};            // video đang mở: {id, type, url, q, qualities, info, related}
  let hls = null, ytp = null, lastActive = 'audio';
  let ytApiP = null, hlsP = null;
  const playerMode = () => LS.get('playerMode', 'own'); // own | yt

  function loadYTApi() {
    if (!ytApiP) ytApiP = new Promise((res, rej) => {
      window.onYouTubeIframeAPIReady = () => res(window.YT);
      const s = document.createElement('script');
      s.src = 'https://www.youtube.com/iframe_api';
      s.onerror = () => { ytApiP = null; rej(new Error('Không tải được trình phát YouTube')); };
      document.head.append(s);
    });
    return ytApiP;
  }
  function loadHlsJs() {
    if (!hlsP) hlsP = new Promise((res) => {
      const s = document.createElement('script');
      s.src = '/vendor/hls.min.js';
      s.onload = () => res(window.Hls || null);
      s.onerror = () => { hlsP = null; res(null); };
      document.head.append(s);
    });
    return hlsP;
  }

  const inPip = () => document.pictureInPictureElement === videoEl || videoEl.webkitPresentationMode === 'picture-in-picture';
  const pipSupported = () => (videoEl.webkitSupportsPresentationMode && videoEl.webkitSupportsPresentationMode('picture-in-picture'))
    || (document.pictureInPictureEnabled && !videoEl.disablePictureInPicture);
  async function togglePip() {
    try {
      if (inPip()) {
        if (document.pictureInPictureElement) await document.exitPictureInPicture();
        else videoEl.webkitSetPresentationMode('inline');
        return;
      }
      if (videoEl.readyState < 1) { toast('Chờ video tải xong một chút rồi bấm lại'); return; }
      if (videoEl.webkitSupportsPresentationMode && videoEl.webkitSupportsPresentationMode('picture-in-picture')) videoEl.webkitSetPresentationMode('picture-in-picture');
      else await videoEl.requestPictureInPicture();
      if (videoEl.paused) videoEl.play().catch(() => {});
    } catch (e) { toast('Không bật được cửa sổ nổi: ' + (e.message || e.name)); }
  }

  function destroyHls() { if (hls) { try { hls.destroy(); } catch {} hls = null; } }
  function destroyYT() { if (ytp) { try { ytp.destroy(); } catch {} ytp = null; } ytbox.innerHTML = ''; }
  function stopVideo() {
    destroyHls(); destroyYT();
    if (inPip()) { try { document.pictureInPictureElement ? document.exitPictureInPicture() : videoEl.webkitSetPresentationMode('inline'); } catch {} }
    videoEl.pause(); videoEl.removeAttribute('src'); videoEl.load();
    V = {};
  }
  function pauseVideo() {
    try { videoEl.pause(); } catch {}
    try { ytp && ytp.pauseVideo && ytp.pauseVideo(); } catch {}
  }
  const videoTime = () => {
    if (ytp && ytp.getCurrentTime) { try { return ytp.getCurrentTime() || 0; } catch { return 0; } }
    return videoEl.currentTime || 0;
  };

  function setVStatus(t) { $('#vStatus').textContent = t || ''; }
  function updateTools() {
    const own = V.type && V.type !== 'yt';
    $('#vPip').hidden = !own || !pipSupported();
    $('#vQ').hidden = !own || !(V.qualities && V.qualities.length > 1);
    $('#vQ').textContent = '⚙ ' + (V.q ? V.q + 'p' : '');
    $('#vMode').textContent = V.type === 'yt' ? '▶ Trình phát riêng' : '▶ Trình phát YouTube';
  }

  function seekWhenReady(t) {
    if (!t) return;
    const tryIt = () => {
      const s = videoEl.seekable;
      if (s.length && s.end(s.length - 1) >= t - 1) {
        videoEl.currentTime = t;
        ['loadedmetadata', 'progress', 'durationchange', 'canplay'].forEach((e) => videoEl.removeEventListener(e, tryIt));
      }
    };
    ['loadedmetadata', 'progress', 'durationchange', 'canplay'].forEach((e) => videoEl.addEventListener(e, tryIt));
  }
  async function setVideoSrc(j, t) {
    destroyHls(); destroyYT();
    videoEl.hidden = false; ytbox.hidden = true;
    if (j.type === 'hls' && !videoEl.canPlayType('application/vnd.apple.mpegurl')) {
      const Hls = await loadHlsJs();
      if (Hls && Hls.isSupported()) {
        hls = new Hls({ maxBufferLength: 30, manifestLoadingTimeOut: 45000, manifestLoadingMaxRetry: 2 });
        hls.on(Hls.Events.ERROR, (_, d) => { if (d.fatal) { setVStatus('⚠ Lỗi phát video (' + d.details + ')'); } });
        hls.loadSource(j.url);
        hls.attachMedia(videoEl);
      } else videoEl.src = j.url;
    } else videoEl.src = j.url;
    seekWhenReady(t);
    videoEl.play().catch(() => {}); // iPhone có thể yêu cầu bấm ▶ trên video
    updateTools();
  }
  async function startOwn(id, t, q) {
    setVStatus('Đang chuẩn bị video…');
    let j;
    try { j = await api(`/play/${id}?q=${q || LS.get('vq', 720)}`, { timeout: 90000 }); }
    catch (e) { if (V.id === id) { setVStatus('⚠ ' + e.message + ' — thử trình phát YouTube'); startYT(id, t); } return; }
    if (V.id !== id) return;
    if (j.type === 'yt') { setVStatus(j.reason || ''); startYT(id, t); return; }
    Object.assign(V, j);
    setVStatus(j.note || (j.type === 'hls' ? `Không quảng cáo · ${j.q}p` : `Không quảng cáo · ${j.q}p (bản có sẵn)`));
    await setVideoSrc(j, t);
  }
  function startYT(id, t) {
    destroyHls();
    videoEl.pause(); videoEl.removeAttribute('src'); videoEl.load();
    videoEl.hidden = true; ytbox.hidden = false;
    ytbox.innerHTML = '<div id="ytp"></div>';
    V.type = 'yt';
    loadYTApi().then((YT) => {
      if (V.id !== id) return;
      ytp = new YT.Player('ytp', {
        host: 'https://www.youtube-nocookie.com', videoId: id,
        playerVars: { playsinline: 1, autoplay: 1, rel: 0, modestbranding: 1, start: Math.floor(t || 0) },
        events: { onStateChange: (e) => { if (e.data === YT.PlayerState.PLAYING) onVideoPlay(); } },
      });
      updateTools();
    }).catch((e) => setVStatus('⚠ ' + e.message));
    updateTools();
  }
  function openVideo(id, t) {
    stopVideo();
    V = { id };
    videoEl.poster = `https://i.ytimg.com/vi/${id}/hqdefault.jpg`;
    if (!navigator.onLine) { videoEl.hidden = false; ytbox.hidden = true; setVStatus('Offline — không xem được video. Bài đã tải nghe ở Thư viện.'); updateTools(); return; }
    if (playerMode() === 'yt') startYT(id, t); else startOwn(id, t);
  }
  function onVideoPlay() {
    lastActive = 'video';
    if (!audio.paused) { userWantsPlay = false; audio.pause(); }
    const it = V.info || videoCache.get(V.id) || {};
    if ('mediaSession' in navigator && V.id) {
      navigator.mediaSession.metadata = new MediaMetadata({
        title: it.title || 'YT Nền', artist: it.channel || '', artwork: [{ src: `https://i.ytimg.com/vi/${V.id}/hqdefault.jpg`, sizes: '480x360', type: 'image/jpeg' }],
      });
    }
  }
  videoEl.addEventListener('play', onVideoPlay);
  // Bấm "quay lại app" trên cửa sổ nổi khi đang ở trang khác -> mở lại trang xem
  const onPipChange = () => {
    if (inPip() || !V.id) return;
    const onWatch = curRoute.name === 'watch' && curRoute.parts[1] === V.id;
    if (onWatch) return;
    if (!videoEl.paused) location.hash = '#/watch/' + V.id;
    else stopVideo();
  };
  videoEl.addEventListener('leavepictureinpicture', onPipChange);
  videoEl.addEventListener('webkitpresentationmodechanged', () => setTimeout(onPipChange, 50));

  $('#vPip').onclick = togglePip;
  $('#vQ').onclick = () => openSheet((b) => {
    b.append(el('<h3>Chất lượng video</h3>'));
    [...(V.qualities || [])].reverse().forEach((q) => b.append(sheetItem(q === V.q ? '●' : '○', `${q}p` + (q >= 720 ? ' HD' : ''), async () => {
      LS.set('vq', q);
      const t = videoEl.currentTime || 0, id = V.id;
      V.q = q; updateTools();
      await startOwn(id, t, q);
    })));
    b.append(el('<p class="muted pad" style="font-size:12px">Từ 480p trở lên máy chủ ghép hình + tiếng; lần đầu mất vài giây.</p>'));
  });
  $('#vMode').onclick = () => {
    const id = V.id, t = videoTime();
    if (!id) return;
    const toYT = V.type !== 'yt';
    LS.set('playerMode', toYT ? 'yt' : 'own');
    if (toYT) { setVStatus('Trình phát YouTube (có thể có quảng cáo)'); startYT(id, t); }
    else { destroyYT(); V.type = ''; updateTools(); startOwn(id, t); }
  };

  function pageWatch(view, { parts, q, alive }) {
    const id = parts[1];
    if (!/^[\w-]{11}$/.test(id || '')) { view.append(emptyEl('Link video không hợp lệ.')); return; }
    vdock.hidden = false;
    document.body.classList.add('watching');
    if (V.id !== id || (!videoEl.currentSrc && !hls && !ytp)) openVideo(id, +q.get('t') || 0);
    else updateTools();
    const v = videoCache.get(id) || { id };
    const w = el(`<div class="watch">
      <div class="winfo">
        <h1></h1><div class="vsub meta1"></div>
        <a class="ch"><span class="av"></span><span class="chname"></span></a>
        <div class="actions">
          <button class="btn primary" data-a="bg">🎧 Nghe nền</button>
          <button class="btn" data-a="save">＋ Lưu</button>
          <button class="btn" data-a="next">⏭ Phát tiếp theo</button>
          <button class="btn" data-a="share">🔗 Sao chép link</button>
        </div>
        <div class="desc" hidden></div>
      </div>
      <h2 class="sec">Video liên quan</h2>
      <div class="list rel"></div></div>`);
    view.append(w);
    let info = { ...v, ...(V.info || {}) }, related = V.related || [];
    const fill = () => {
      $('h1', w).textContent = info.title || '…';
      $('.meta1', w).textContent = [fmtViews(info.views), fmtDate(info.uploadDate)].filter(Boolean).join(' · ');
      $('.chname', w).textContent = info.channel || '';
      $('.av', w).textContent = (info.channel || '?').trim().charAt(0).toUpperCase();
      $('.ch', w).href = info.channelId ? '#/channel/' + encodeURIComponent(info.channelId) : '#/';
      const d = $('.desc', w);
      d.textContent = info.description || ''; d.hidden = !info.description;
    };
    fill();
    $('.desc', w).onclick = (e) => e.currentTarget.classList.toggle('open');

    cleanup = () => {
      document.body.classList.remove('watching');
      vdock.hidden = true;
      if (!inPip()) stopVideo(); // đang ở cửa sổ nổi thì phát tiếp
    };

    $$('.actions button', w).forEach((b) => {
      b.onclick = () => {
        const a = b.dataset.a;
        if (a === 'bg') {
          const t = videoTime();
          pauseVideo();
          playQueue([info, ...related], 0, { from: 'Từ video', startAt: t });
          toast('🎧 Đang nghe nền — có thể tắt màn hình.');
        } else if (a === 'save') addSheet(info);
        else if (a === 'next') playNext(info);
        else if (a === 'share') {
          const link = 'https://youtu.be/' + id;
          (navigator.clipboard ? navigator.clipboard.writeText(link) : Promise.reject()).then(() => toast('Đã sao chép link'), () => prompt('Link:', link));
        }
      };
    });

    const rel = $('.rel', w);
    if (!navigator.onLine) { rel.append(emptyEl('Offline.')); return; }
    if (related.length) related.forEach((x) => rel.append(lrow(x)));
    else rel.append(loadingEl());
    api('/video/' + id, { timeout: 90000 }).then((j) => {
      remember(slim(j));
      info = { ...info, ...j };
      if (V.id === id) V.info = info;
      if (alive()) fill();
      api('/history', { method: 'POST', body: slim(info) }).catch(() => {});
      sessionStorage.removeItem('ytbg2:home');
    }).catch((e) => { if (alive()) toast('⚠ ' + e.message); });
    if (related.length) return;
    api('/related/' + id, { timeout: 120000 }).then((j) => {
      if (V.id === id) V.related = j.items;
      if (!alive()) return;
      related = j.items;
      rel.innerHTML = '';
      if (!related.length) rel.append(emptyEl('Không có video liên quan.'));
      related.forEach((x) => rel.append(lrow(x)));
    }).catch((e) => { if (alive()) { rel.innerHTML = ''; rel.append(emptyEl('⚠ ' + esc(e.message))); } });
  }

  // ---------- kênh ----------
  function pageChannel(view, { parts, alive }) {
    const cid = parts[1];
    const head = el('<div class="plhead"><h1>…</h1><div class="row"><button class="btn primary" disabled>🎧 Nghe tất cả</button><button class="btn" disabled>🔀 Trộn</button></div></div>');
    const list = el('<div class="list"></div>');
    const more = el('<div class="pad" style="margin:12px 0"><button class="btn" style="width:100%;justify-content:center">Xem thêm</button></div>');
    more.hidden = true;
    view.append(head, list, more);
    list.append(loadingEl());
    let items = [], next = null;
    const load = async (page) => {
      const j = await api(`/channel/${encodeURIComponent(cid)}?page=${page}`, { timeout: 90000 });
      if (!alive()) return;
      $('h1', head).textContent = j.name || 'Kênh';
      if (page === 1) list.innerHTML = '';
      j.items.forEach((x) => { items.push(x); list.append(lrow(x)); });
      if (!items.length) list.append(emptyEl('Kênh chưa có video.'));
      next = j.nextpage; more.hidden = !next;
      $$('button', head).forEach((b) => { b.disabled = !items.length; });
    };
    const [bAll, bShuf] = $$('button', head);
    bAll.onclick = () => playQueue(items, 0, { from: $('h1', head).textContent });
    bShuf.onclick = () => playQueue(items, Math.floor(Math.random() * items.length), { from: $('h1', head).textContent, shuffle: true });
    $('button', more).onclick = guard(async () => { $('button', more).disabled = true; try { await load(next); } finally { $('button', more).disabled = false; } });
    load(1).catch((e) => { if (alive()) { list.innerHTML = ''; list.append(emptyEl('⚠ ' + esc(e.message))); } });
  }

  // ================================================================ Phần 2 — HỒ SƠ / THƯ VIỆN
  let playlists = LS.get('playlists', []);
  let playlistsFresh = false;
  let lastSync = 0;

  function setPlaylists(list) {
    playlists = list;
    playlistsFresh = true;
    LS.set('playlists', list);
    rerenderIf('library', 'playlist');
    syncDownloads();
  }
  async function syncPlaylists() {
    lastSync = Date.now();
    try { setPlaylists((await api('/playlists')).playlists); } catch {}
  }
  const plName = (pid) => (playlists.find((p) => p.id === pid) || {}).name || '';
  const createPlaylist = guard(async (name, item) => {
    const j = await api('/playlists', { method: 'POST', body: { name, item: item ? slim(item) : undefined } });
    setPlaylists(j.playlists);
    toast(item ? `Đã lưu vào “${name}” — bài sẽ tự tải về máy` : `Đã tạo playlist “${name}”`);
    return j.playlist;
  });
  const addToPlaylist = guard(async (pid, v) => {
    const j = await api(`/playlists/${pid}/items`, { method: 'POST', body: slim(v) });
    setPlaylists(j.playlists);
    toast(`Đã lưu vào “${plName(pid)}” — bài sẽ tự tải về máy`);
  });
  const removeFromPlaylist = guard(async (pid, vid) => {
    const j = await api(`/playlists/${pid}/items/${vid}`, { method: 'DELETE' });
    setPlaylists(j.playlists);
    toast(`Đã xoá khỏi “${plName(pid)}”`);
  });
  const renamePlaylist = guard(async (pid) => {
    const name = prompt('Tên mới:', plName(pid));
    if (!name || !name.trim()) return;
    setPlaylists((await api(`/playlists/${pid}`, { method: 'PATCH', body: { name: name.trim() } })).playlists);
  });
  const deletePlaylist = guard(async (pid) => {
    if (!confirm(`Xoá playlist “${plName(pid)}”? Bài chỉ có trong playlist này sẽ bị xoá khỏi máy.`)) return;
    setPlaylists((await api(`/playlists/${pid}`, { method: 'DELETE' })).playlists);
    location.hash = '#/library';
  });
  const movePlaylistItem = guard(async (pid, vid, dir) => {
    const pl = playlists.find((p) => p.id === pid);
    const ids = pl.items.map((x) => x.id);
    const i = ids.indexOf(vid), j = i + dir;
    if (i < 0 || j < 0 || j >= ids.length) return;
    [ids[i], ids[j]] = [ids[j], ids[i]];
    setPlaylists((await api(`/playlists/${pid}`, { method: 'PATCH', body: { order: ids } })).playlists);
  });

  function dlButton(id) {
    const b = el(`<button class="dl" data-dl="${id}"></button>`);
    paintDlBtn(b, id);
    b.onclick = (e) => { e.stopPropagation(); dlClick(id); };
    return b;
  }
  function plSummary(pl) {
    const n = pl.items.length, d = pl.items.filter((x) => localIds.has(x.id)).length;
    return `${n} bài` + (n ? ` · 📥 ${d}/${n} đã tải` : '');
  }

  function pageLibrary(view) {
    const acc = el(`<div class="card"><div class="row" style="justify-content:space-between">
        <div><div style="font-weight:600">👤 ${esc(me.user)}${me.admin ? ' <span class="muted">(admin)</span>' : ''}</div></div>
        <div class="row"><button class="btn sm" data-a="pw">Đổi mật khẩu</button><button class="btn sm danger" data-a="out">Đăng xuất</button></div>
      </div></div>`);
    $('[data-a=pw]', acc).onclick = changePasswordSheet;
    $('[data-a=out]', acc).onclick = logout;
    const h = el('<h2 class="sec"><span>Playlist của tôi</span><button class="btn sm">＋ Tạo playlist</button></h2>');
    $('button', h).onclick = () => { const n = prompt('Tên playlist mới:'); if (n && n.trim()) createPlaylist(n.trim()); };
    const list = el('<div></div>');
    if (!playlists.length) list.append(emptyEl('Chưa có playlist nào.<br>Bấm ⋮ hoặc “＋ Lưu” ở bất kỳ video nào để thêm vào playlist — bài sẽ tự tải về máy để nghe offline.'));
    for (const pl of playlists) {
      const first = pl.items[0];
      const r = el(`<a class="plrow" href="#/playlist/${pl.id}">
        <div class="plcover" style="${first ? `background-image:url('${esc(thumbOf(first.id))}')` : ''}"><span>${pl.items.length}</span></div>
        <div class="txt" style="flex:1;min-width:0"><div class="vtitle ell"></div><div class="vsub" data-plsum="${pl.id}"></div></div></a>`);
      $('.vtitle', r).textContent = pl.name;
      $('[data-plsum]', r).textContent = plSummary(pl);
      list.append(r);
    }
    const hist = el('<a class="plrow" href="#/history"><div class="plcover" style="display:flex;align-items:center;justify-content:center;font-size:26px">🕘</div><div class="txt"><div class="vtitle">Lịch sử xem</div><div class="vsub">Video bạn đã mở gần đây</div></div></a>');
    const dl = el(`<div class="card"><h3>📥 Tải về máy</h3>
      <div class="muted" data-dlsum style="font-size:14px"></div>
      <label class="toggle"><input type="checkbox" data-o="autoDl"> Tự tải mọi bài trong playlist về máy</label>
      <label class="toggle"><input type="checkbox" data-o="saver"> Tiết kiệm dung lượng (chất lượng thấp hơn, ~1/3 dung lượng)</label>
      <div class="row" style="margin-top:6px"><button class="btn sm" data-a="retry">↻ Tải lại bài lỗi</button><button class="btn sm danger" data-a="wipe">Xoá toàn bộ bản tải</button></div>
      <p class="muted" style="font-size:12px;margin:10px 0 0">Bài được lưu trong bộ nhớ trình duyệt của máy này, phát được khi không có mạng. Trên iPhone nên “Thêm vào MH chính” để iOS không tự xoá dữ liệu.</p></div>`);
    $$('[data-o]', dl).forEach((c) => {
      const k = c.dataset.o;
      c.checked = LS.get(k, k === 'autoDl');
      c.onchange = () => { LS.set(k, c.checked); if (k === 'autoDl' && c.checked) syncDownloads(); };
    });
    $('[data-a=retry]', dl).onclick = () => { for (const [id, s] of dlState) if (s === 'error') dlState.delete(id); syncDownloads(true); };
    $('[data-a=wipe]', dl).onclick = async () => {
      if (!localIds.size || !confirm(`Xoá ${localIds.size} bài đã tải khỏi máy này? (Playlist vẫn giữ nguyên)`)) return;
      LS.set('autoDl', false);
      dlQueue.length = 0;
      for (const id of [...localIds]) await deleteLocal(id);
      route(true);
    };
    view.append(acc, h, list, hist, dl);
    updateDlSummary();
  }

  function pagePlaylist(view, { parts }) {
    const pl = playlists.find((p) => p.id === parts[1]);
    if (!pl) { view.append(emptyEl('Không tìm thấy playlist. <a href="#/library" style="color:var(--accent)">Về Thư viện</a>')); return; }
    const head = el(`<div class="plhead"><h1></h1><div class="vsub" data-plsum="${pl.id}"></div>
      <div class="row"><button class="btn primary" data-a="play">▶ Phát</button><button class="btn" data-a="shuf">🔀 Trộn</button><button class="btn" data-a="more">⋯</button></div></div>`);
    $('h1', head).textContent = pl.name;
    $('[data-plsum]', head).textContent = plSummary(pl);
    const items = pl.items;
    $('[data-a=play]', head).onclick = () => items.length && playQueue(items, 0, { from: pl.name });
    $('[data-a=shuf]', head).onclick = () => items.length && playQueue(items, Math.floor(Math.random() * items.length), { from: pl.name, shuffle: true });
    $('[data-a=more]', head).onclick = () => openSheet((b) => {
      b.append(sheetItem('✎', 'Đổi tên', () => renamePlaylist(pl.id)));
      b.append(sheetItem('📥', 'Tải tất cả về máy', () => { items.forEach((x) => enqueueDl(x.id)); }));
      b.append(sheetItem('🗑', 'Xoá playlist', () => deletePlaylist(pl.id)));
    });
    const list = el('<div class="list"></div>');
    if (!items.length) list.append(emptyEl('Playlist trống. Bấm “＋ Lưu” ở trang video để thêm bài.'));
    items.forEach((v, i) => {
      const right = el('<div class="row" style="flex:none;gap:2px"></div>');
      right.append(dlButton(v.id));
      const m = el('<button class="more" aria-label="Tuỳ chọn">⋮</button>');
      m.onclick = () => openSheet((b) => {
        const h = el('<h3 class="ell"></h3>'); h.textContent = v.title; b.append(h);
        b.append(sheetItem('⏭', 'Phát tiếp theo', () => playNext(v)));
        b.append(sheetItem('💾', 'Lưu file vào máy', () => saveFile(v)));
        if (i > 0) b.append(sheetItem('⬆', 'Chuyển lên', () => movePlaylistItem(pl.id, v.id, -1)));
        if (i < items.length - 1) b.append(sheetItem('⬇', 'Chuyển xuống', () => movePlaylistItem(pl.id, v.id, 1)));
        b.append(sheetItem('▶', 'Xem video', () => { location.hash = '#/watch/' + v.id; }));
        b.append(sheetItem('＋', 'Lưu vào playlist khác', () => addSheet(v)));
        b.append(sheetItem('✕', 'Xoá khỏi playlist', () => removeFromPlaylist(pl.id, v.id)));
      });
      right.append(m);
      list.append(lrow(v, { onClick: () => playQueue(items, i, { from: pl.name }), right, cur: Q.items[Q.idx] && Q.items[Q.idx].id === v.id && Q.from === pl.name }));
    });
    view.append(head, list);
  }

  function pageHistory(view, { alive }) {
    const h = el('<h2 class="sec"><span>Lịch sử xem</span><button class="btn sm">Xoá lịch sử</button></h2>');
    const list = el('<div class="list"></div>');
    view.append(h, list);
    list.append(loadingEl());
    $('button', h).onclick = guard(async () => {
      if (!confirm('Xoá toàn bộ lịch sử xem?')) return;
      await api('/history', { method: 'DELETE' });
      sessionStorage.removeItem('ytbg2:home');
      route(true);
    });
    api('/history').then((j) => {
      if (!alive()) return;
      list.innerHTML = '';
      if (!j.items.length) list.append(emptyEl('Chưa có lịch sử.'));
      j.items.forEach((v) => list.append(lrow(v)));
    }).catch((e) => { if (alive()) { list.innerHTML = ''; list.append(emptyEl('⚠ ' + esc(e.message))); } });
  }

  function changePasswordSheet() {
    openSheet((b) => {
      const f = el(`<form class="form pad"><h3 style="margin:8px 0">Đổi mật khẩu</h3>
        <input type="password" name="old" placeholder="Mật khẩu hiện tại" autocomplete="current-password" required>
        <input type="password" name="n1" placeholder="Mật khẩu mới (≥ 6 ký tự)" autocomplete="new-password" required minlength="6">
        <input type="password" name="n2" placeholder="Nhập lại mật khẩu mới" autocomplete="new-password" required>
        <div class="err"></div><button class="btn primary" style="justify-content:center">Lưu</button></form>`);
      f.onsubmit = async (e) => {
        e.preventDefault();
        const d = Object.fromEntries(new FormData(f));
        if (d.n1 !== d.n2) { $('.err', f).textContent = 'Hai mật khẩu mới không khớp'; return; }
        try { await api('/password', { method: 'POST', body: { old: d.old, new: d.n1 } }); closeSheet(); toast('Đã đổi mật khẩu. Các thiết bị khác đã bị đăng xuất.'); }
        catch (err) { $('.err', f).textContent = err.message; }
      };
      b.append(f);
    });
  }

  // ---------- quản trị ----------
  function pageAdmin(view, { alive }) {
    if (!me.admin) { location.hash = '#/'; return; }
    const form = el(`<form class="card form"><h3>Tạo tài khoản</h3>
      <input name="username" placeholder="Tên đăng nhập (a-z, 0-9, . _ -)" autocapitalize="none" spellcheck="false" required>
      <input name="password" type="password" placeholder="Mật khẩu (≥ 6 ký tự)" autocomplete="new-password" required minlength="6">
      <label class="toggle"><input type="checkbox" name="admin"> Quyền admin (quản lý tài khoản)</label>
      <div class="err"></div><button class="btn primary" style="justify-content:center">Tạo</button></form>`);
    const box = el('<div class="card"><h3>Tài khoản</h3><div class="tbl"></div></div>');
    view.append(form, box);
    const draw = (users) => {
      const t = el('<table class="users"></table>');
      users.forEach((u) => {
        const tr = el(`<tr><td><b></b> ${u.admin ? '<span class="muted">admin</span>' : ''}</td><td style="text-align:right"><button class="btn sm">⋯</button></td></tr>`);
        $('b', tr).textContent = u.username;
        $('button', tr).onclick = () => openSheet((b) => {
          const h = el('<h3></h3>'); h.textContent = u.username; b.append(h);
          b.append(sheetItem('🔑', 'Đặt lại mật khẩu', guard(async () => {
            const pw = prompt(`Mật khẩu mới cho ${u.username} (≥ 6 ký tự):`);
            if (!pw) return;
            draw((await api(`/admin/users/${u.username}`, { method: 'PATCH', body: { password: pw } })).users);
            toast('Đã đặt lại mật khẩu');
          })));
          if (u.username !== me.user) {
            b.append(sheetItem('🛠', u.admin ? 'Bỏ quyền admin' : 'Cấp quyền admin', guard(async () => {
              draw((await api(`/admin/users/${u.username}`, { method: 'PATCH', body: { admin: !u.admin } })).users);
            })));
            b.append(sheetItem('🗑', 'Xoá tài khoản', guard(async () => {
              if (!confirm(`Xoá tài khoản ${u.username}? Playlist của tài khoản vẫn được giữ trên máy chủ.`)) return;
              draw((await api(`/admin/users/${u.username}`, { method: 'DELETE' })).users);
            })));
          }
        });
        t.append(tr);
      });
      const c = $('.tbl', box); c.innerHTML = ''; c.append(t);
    };
    form.onsubmit = async (e) => {
      e.preventDefault();
      const d = new FormData(form);
      try {
        draw((await api('/admin/users', { method: 'POST', body: { username: d.get('username'), password: d.get('password'), admin: !!d.get('admin') } })).users);
        form.reset(); $('.err', form).textContent = ''; toast('Đã tạo tài khoản');
      } catch (err) { $('.err', form).textContent = err.message; }
    };
    $('.tbl', box).append(loadingEl());
    api('/admin/users').then((j) => alive() && draw(j.users)).catch((e) => toast('⚠ ' + e.message));
  }

  // ================================================================ lưu offline (IndexedDB)
  const localIds = new Set();
  const localThumbs = new Map();
  const dlState = new Map(); // id -> 'queued' | % | 'error'
  const dlQueue = [];
  let dlBusy = false, offlineReady = false, dbP = null;

  function db() {
    if (!dbP) dbP = new Promise((res, rej) => {
      const r = indexedDB.open('ytbg-' + me.user, 1);
      r.onupgradeneeded = () => { r.result.createObjectStore('audio'); r.result.createObjectStore('thumb'); };
      r.onsuccess = () => res(r.result);
      r.onerror = () => rej(r.error);
    });
    return dbP;
  }
  async function idb(name, rw, fn) {
    const d = await db();
    return new Promise((res, rej) => {
      const tx = d.transaction(name, rw ? 'readwrite' : 'readonly');
      const req = fn(tx.objectStore(name));
      tx.oncomplete = () => res(req && req.result);
      tx.onerror = tx.onabort = () => rej(tx.error);
    });
  }
  const getLocal = (id) => idb('audio', false, (s) => s.get(id)).catch(() => null);

  async function initOffline() {
    try {
      (await idb('audio', false, (s) => s.getAllKeys())).forEach((k) => localIds.add(k));
      const keys = await idb('thumb', false, (s) => s.getAllKeys());
      const vals = await idb('thumb', false, (s) => s.getAll());
      keys.forEach((k, i) => localThumbs.set(k, URL.createObjectURL(vals[i])));
    } catch (e) { toast('Không mở được bộ nhớ offline: ' + e.message); }
    offlineReady = true;
    rerenderIf('library', 'playlist');
    syncDownloads();
  }

  function wantedIds() {
    const s = new Set();
    playlists.forEach((p) => p.items.forEach((x) => s.add(x.id)));
    return s;
  }
  function syncDownloads(force) {
    if (!offlineReady) return;
    const want = wantedIds();
    if (LS.get('autoDl', true) || force) {
      for (const id of want) if (!localIds.has(id) && !dlState.has(id)) enqueueDl(id);
    }
    if (playlistsFresh) { // dọn bài không còn trong playlist nào
      for (const id of [...localIds]) if (!want.has(id) && !(Q.items[Q.idx] && Q.items[Q.idx].id === id && !audio.paused)) deleteLocal(id);
    }
    updateDlSummary();
  }
  function dlLabel(id) {
    if (localIds.has(id)) return ['✓', 'done', 'Đã tải về máy'];
    const st = dlState.get(id);
    if (st === 'queued') return ['…', '', 'Đang chờ tải'];
    if (st === 'error') return ['⚠', 'err', 'Tải lỗi — bấm để thử lại'];
    if (typeof st === 'number') return [st + '%', '', 'Đang tải'];
    return ['⬇', '', 'Tải về máy'];
  }
  function paintDlBtn(b, id) { const [t, c, title] = dlLabel(id); b.textContent = t; b.className = 'dl ' + c; b.title = title; }
  function paintDl(id) {
    $$(`[data-dl="${id}"]`).forEach((b) => paintDlBtn(b, id));
    $$('[data-plsum]').forEach((s) => { const pl = playlists.find((p) => p.id === s.dataset.plsum); if (pl) s.textContent = plSummary(pl); });
  }
  function dlClick(id) {
    if (localIds.has(id)) {
      const v = playlists.flatMap((p) => p.items).find((x) => x.id === id) || { id };
      openSheet((b) => {
        b.append(sheetItem('💾', 'Lưu file vào máy', () => saveFile(v)));
        b.append(sheetItem('🗑', 'Xoá bản tải (tải lại sau)', () => { dlState.set(id, 'error'); deleteLocal(id); }));
      });
      return;
    }
    const st = dlState.get(id);
    if (st === 'queued' || typeof st === 'number') return;
    dlState.delete(id);
    enqueueDl(id, true);
  }
  function enqueueDl(id, front) {
    if (!offlineReady || localIds.has(id)) return;
    const st = dlState.get(id);
    if (st === 'queued' || typeof st === 'number') return;
    dlState.set(id, 'queued');
    front ? dlQueue.unshift(id) : dlQueue.push(id);
    paintDl(id);
    pumpDl();
  }
  let persistAsked = false;
  async function pumpDl() {
    if (dlBusy) return;
    dlBusy = true;
    if (!persistAsked) { persistAsked = true; try { navigator.storage && navigator.storage.persist && navigator.storage.persist(); } catch {} }
    try {
      while (dlQueue.length) {
        if (!navigator.onLine) await new Promise((r) => window.addEventListener('online', r, { once: true }));
        const id = dlQueue.shift();
        if (localIds.has(id) || dlState.get(id) !== 'queued') continue;
        dlState.set(id, 0); paintDl(id); updateDlSummary();
        try {
          await downloadTrack(id);
          dlState.delete(id);
          if (!wantedIds().has(id)) deleteLocal(id);
        } catch (e) {
          if (e && e.name === 'QuotaExceededError') {
            toast('Hết dung lượng lưu trữ trên máy — đã dừng tải.', 5000);
            dlQueue.splice(0).forEach((x) => dlState.delete(x));
          }
          dlState.set(id, 'error');
          console.warn('tải lỗi', id, e);
        }
        paintDl(id); updateDlSummary();
      }
    } finally { dlBusy = false; }
  }
  async function fetchBlob(url, type, onProg) {
    const ctl = new AbortController();
    let timer;
    const kick = () => { clearTimeout(timer); timer = setTimeout(() => ctl.abort(), 45000); };
    kick();
    try {
      const r = await fetch(url, { signal: ctl.signal, credentials: 'same-origin' });
      if (!r.ok) throw new Error('HTTP ' + r.status);
      const total = +r.headers.get('Content-Length') || 0;
      if (!r.body) return await r.blob();
      const reader = r.body.getReader();
      const parts = [];
      let got = 0, last = -1;
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        parts.push(value); got += value.length; kick();
        const p = total ? Math.min(99, Math.floor((got * 100) / total)) : -1;
        if (p !== last && p >= 0) { last = p; onProg(p); }
      }
      if (total && got < total) throw new Error('Tải thiếu dữ liệu');
      return new Blob(parts, { type });
    } finally { clearTimeout(timer); }
  }
  async function downloadTrack(id) {
    const info = await api('/video/' + id, { timeout: 90000 });
    const f = pickAudio(info.audio);
    if (!f) throw new Error('Không có định dạng âm thanh phù hợp');
    const blob = await fetchBlob(f.url, f.mime, (p) => { dlState.set(id, p); paintDl(id); });
    if (blob.size < 20000) throw new Error('File quá nhỏ');
    await idb('audio', true, (s) => s.put({
      blob, mime: f.mime, ext: f.ext || 'm4a', bitrate: f.bitrate, size: blob.size, t: Date.now(),
      title: info.title, channel: info.channel, duration: info.duration,
    }, id));
    localIds.add(id);
    try {
      const r = await fetch('/api/thumb/' + id, { credentials: 'same-origin' });
      if (r.ok) {
        const tb = await r.blob();
        await idb('thumb', true, (s) => s.put(tb, id));
        localThumbs.set(id, URL.createObjectURL(tb));
      }
    } catch {}
  }
  async function deleteLocal(id) {
    localIds.delete(id);
    const u = localThumbs.get(id);
    if (u) { URL.revokeObjectURL(u); localThumbs.delete(id); }
    try { await idb('audio', true, (s) => s.delete(id)); await idb('thumb', true, (s) => s.delete(id)); } catch {}
    paintDl(id); updateDlSummary();
  }
  async function updateDlSummary() {
    const box = $('[data-dlsum]');
    if (!box) return;
    const want = wantedIds();
    const have = [...want].filter((id) => localIds.has(id)).length;
    const busy = [...dlState.values()].filter((s) => s === 'queued' || typeof s === 'number').length;
    const errs = [...dlState.values()].filter((s) => s === 'error').length;
    let t = `Đã tải ${have}/${want.size} bài` + (busy ? ` · đang tải ${busy}` : '') + (errs ? ` · ⚠ ${errs} lỗi` : '');
    try {
      const est = navigator.storage && navigator.storage.estimate && (await navigator.storage.estimate());
      if (est && est.usage != null) t += ` · dùng ${fmtMB(est.usage)}` + (est.quota ? ` / còn trống ~${fmtMB(Math.max(0, est.quota - est.usage))}` : '');
    } catch {}
    box.textContent = t;
  }
  async function saveFile(v) {
    let rec = await getLocal(v.id);
    if (!rec) {
      if (!navigator.onLine) { toast('Bài chưa tải về máy.'); return; }
      toast('Đang tải file…');
      try { await downloadTrack(v.id); } catch (e) { toast('⚠ ' + e.message); return; }
      paintDl(v.id);
      rec = await getLocal(v.id);
      if (!rec) return;
    }
    const name = (rec.title || v.title || v.id).replace(/[\\/:*?"<>|]+/g, ' ').trim().slice(0, 120) || v.id;
    const a = document.createElement('a');
    a.href = URL.createObjectURL(rec.blob);
    a.download = `${name}.${rec.ext || 'm4a'}`;
    document.body.append(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 60000);
  }

  // ================================================================ trình phát âm thanh (nghe nền)
  const audio = document.createElement('audio');
  audio.preload = 'auto';
  audio.setAttribute('playsinline', '');
  audio.setAttribute('webkit-playsinline', '');
  document.body.append(audio);

  let Q = LS.get('queue', { items: [], idx: -1, from: '' });
  let shuffle = LS.get('shuffle', false), repeat = LS.get('repeat', 'all'); // off | all | one
  let userWantsPlay = false, loadToken = 0, loadedId = null, srcKind = '', objUrl = null;
  let probing = false, hiddenAt = 0, errRetry = 0;

  const SILENT = (() => { // 0,5 giây im lặng để "mở khoá" âm thanh trên iPhone
    const n = 4000, buf = new ArrayBuffer(44 + n), v = new DataView(buf);
    const w = (o, s) => [...s].forEach((c, i) => v.setUint8(o + i, c.charCodeAt(0)));
    w(0, 'RIFF'); v.setUint32(4, 36 + n, true); w(8, 'WAVE'); w(12, 'fmt ');
    v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
    v.setUint32(24, 8000, true); v.setUint32(28, 8000, true); v.setUint16(32, 1, true); v.setUint16(34, 8, true);
    w(36, 'data'); v.setUint32(40, n, true);
    for (let i = 0; i < n; i++) v.setUint8(44 + i, 128);
    return URL.createObjectURL(new Blob([buf], { type: 'audio/wav' }));
  })();
  let primed = false;
  function prime() { // gọi trong lúc người dùng chạm
    setAudioSession();
    if (primed) return;
    primed = true;
    if (!audio.src) { audio.src = SILENT; audio.play().then(() => { if (audio.src === SILENT) audio.pause(); }).catch(() => {}); }
  }
  function setAudioSession() {
    try { if (navigator.audioSession && navigator.audioSession.type !== 'playback') navigator.audioSession.type = 'playback'; } catch {}
  }

  const canPlay = (m) => !!m && audio.canPlayType(m) !== '';
  function pickAudio(list) {
    const ok = (list || []).filter((a) => canPlay(a.codec ? `${a.mime}; codecs="${a.codec}"` : a.mime) || canPlay(a.mime));
    if (!ok.length) return null;
    if (LS.get('saver', false)) return ok.sort((a, b) => a.bitrate - b.bitrate)[0];
    const mp4 = ok.filter((a) => a.mime === 'audio/mp4');
    return (mp4.length ? mp4 : ok).sort((a, b) => b.bitrate - a.bitrate)[0];
  }

  const curItem = () => Q.items[Q.idx];
  function saveQ() { LS.set('queue', Q); }

  function playQueue(items, start = 0, { from = '', startAt = 0, shuffle: sh = false } = {}) {
    prime();
    pauseVideo();
    lastActive = 'audio';
    items = items.filter((x) => x && x.id).map(slim);
    if (!items.length) return;
    const first = items[start];
    if (sh) {
      const rest = items.filter((_, i) => i !== start);
      for (let i = rest.length - 1; i > 0; i--) { const j = Math.floor(Math.random() * (i + 1)); [rest[i], rest[j]] = [rest[j], rest[i]]; }
      items = [first, ...rest]; start = 0;
    }
    shuffle = sh; LS.set('shuffle', sh);
    Q = { items, idx: start, from };
    saveQ();
    loadCurrent(true, startAt);
  }
  function playNext(v) {
    prime();
    if (!curItem()) { playQueue([v], 0, { from: 'Nghe nền' }); return; }
    Q.items = Q.items.filter((x, i) => x.id !== v.id || i === Q.idx);
    Q.items.splice(Q.idx + 1, 0, slim(v));
    saveQ(); updatePlayerUI();
    toast('Sẽ phát tiếp theo: ' + (v.title || v.id));
  }

  function trySrc(url, autoplay) {
    return new Promise((resolve) => {
      let done = false;
      const fin = (ok) => {
        if (done) return; done = true; clearTimeout(t);
        audio.removeEventListener('loadedmetadata', onOk); audio.removeEventListener('playing', onOk); audio.removeEventListener('error', onErr);
        resolve(ok);
      };
      const onOk = () => fin(true), onErr = () => fin(false);
      audio.addEventListener('loadedmetadata', onOk);
      audio.addEventListener('playing', onOk);
      audio.addEventListener('error', onErr);
      const t = setTimeout(() => fin(false), 25000);
      audio.src = url;
      if (autoplay) audio.play().catch((e) => { if (e.name === 'NotSupportedError') fin(false); });
      else audio.load();
    });
  }

  async function loadCurrent(autoplay, startAt = 0) {
    const it = curItem();
    if (!it) return;
    const token = ++loadToken;
    if (autoplay) userWantsPlay = true;
    loadedId = null;
    updatePlayerUI(); updateMediaSession();
    // offline mà bài chưa tải -> sang bài đã tải kế tiếp
    if (!localIds.has(it.id) && !navigator.onLine) {
      const n = Q.items.length;
      for (let j = 1; j < n; j++) {
        const k = (Q.idx + j) % n;
        if (k < Q.idx && repeat === 'off') break;
        if (localIds.has(Q.items[k].id)) { Q.idx = k; saveQ(); toast('Offline — bỏ qua bài chưa tải'); return loadCurrent(autoplay); }
      }
      userWantsPlay = false; audio.pause();
      setSrcText('⚠ Offline và bài này chưa tải về máy');
      return;
    }
    setSrcText('Đang tải…');
    probing = true;
    try {
      let url = null, label = '';
      if (localIds.has(it.id)) {
        const rec = await getLocal(it.id);
        if (token !== loadToken) return;
        if (rec && rec.blob) {
          if (objUrl) URL.revokeObjectURL(objUrl);
          objUrl = url = URL.createObjectURL(rec.blob);
          label = '📥 Phát từ bản đã tải (không tốn mạng)';
          srcKind = 'local';
          if (!it.title && rec.title) { it.title = rec.title; it.channel = rec.channel; }
        }
      }
      if (!url) {
        let info;
        try { info = await api('/video/' + it.id, { timeout: 90000 }); }
        catch (e) {
          if (token !== loadToken) return;
          setSrcText('⚠ ' + e.message); toast('Không phát được: ' + e.message);
          setTimeout(() => { if (token === loadToken && userWantsPlay) next(true); }, 2500);
          return;
        }
        if (token !== loadToken) return;
        if (!it.title) { Object.assign(it, slim(info)); saveQ(); updatePlayerUI(); updateMediaSession(); }
        const f = pickAudio(info.audio);
        if (!f) { setSrcText('⚠ Không có định dạng phát được'); return; }
        url = f.url; srcKind = 'net';
        label = `🎧 Phát qua máy chủ · ${Math.round(f.bitrate / 1000)} kbps`;
      }
      const ok = await trySrc(url, autoplay);
      if (token !== loadToken) return;
      if (!ok) {
        setSrcText('⚠ Không phát được bài này');
        if (autoplay) setTimeout(() => { if (token === loadToken && userWantsPlay) next(true); }, 2000);
        return;
      }
      loadedId = it.id; errRetry = 0;
      if (startAt) { try { audio.currentTime = startAt; } catch {} }
      setSrcText(label);
      prefetchNext();
    } finally { if (token === loadToken) probing = false; }
  }
  function prefetchNext() { // làm nóng cache máy chủ cho bài kế tiếp
    const n = Q.items[Q.idx + 1];
    if (n && !localIds.has(n.id) && navigator.onLine) api('/video/' + n.id, { timeout: 90000 }).catch(() => {});
  }

  function togglePlay() {
    if (!curItem()) return;
    lastActive = 'audio';
    pauseVideo();
    const playing = loadedId && !audio.paused;
    prime();
    if (playing) { userWantsPlay = false; audio.pause(); return; }
    userWantsPlay = true;
    if (loadedId !== curItem().id) { const p = LS.get('pos', null); loadCurrent(true, p && p.id === curItem().id ? p.t : 0); }
    else audio.play().catch(() => {});
  }
  function next(auto) {
    if (!Q.items.length) return;
    if (auto && repeat === 'one' && loadedId) { audio.currentTime = 0; audio.play().catch(() => {}); return; }
    let i = Q.idx + 1;
    if (i >= Q.items.length) {
      if (repeat === 'off' && auto) { userWantsPlay = false; updatePlayerUI(); return; }
      i = 0;
    }
    Q.idx = i; saveQ();
    loadCurrent(true);
  }
  function prev() {
    if (!Q.items.length) return;
    if (audio.currentTime > 4 && loadedId) { audio.currentTime = 0; return; }
    Q.idx = (Q.idx - 1 + Q.items.length) % Q.items.length; saveQ();
    loadCurrent(true);
  }
  function restoreQueue() { if (curItem()) updatePlayerUI(); }

  // sự kiện <audio>
  const isSilent = () => audio.src === SILENT;
  audio.addEventListener('playing', () => {
    if (isSilent()) return;
    setPlayIcons(true);
    if ('mediaSession' in navigator) navigator.mediaSession.playbackState = 'playing';
  });
  audio.addEventListener('pause', () => {
    if (isSilent() || probing) return;
    // iOS đôi khi dừng nhạc ngay lúc khoá máy -> phát lại (chỉ trong vài giây đầu)
    if (userWantsPlay && document.hidden && !audio.ended && Date.now() - hiddenAt < 4000) {
      setTimeout(() => { if (userWantsPlay && audio.paused) { setAudioSession(); audio.play().catch(() => {}); } }, 300);
      return;
    }
    setPlayIcons(false);
    if ('mediaSession' in navigator) navigator.mediaSession.playbackState = 'paused';
  });
  audio.addEventListener('ended', () => { if (!isSilent()) next(true); });
  audio.addEventListener('error', () => {
    if (isSilent() || probing || !loadedId || !userWantsPlay) return;
    if (errRetry++ < 2) { const t = audio.currentTime; setSrcText('Mất kết nối — đang thử lại…'); setTimeout(() => loadCurrent(true, t), 1500); }
    else next(true);
  });
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) hiddenAt = Date.now();
    else if (Date.now() - lastSync > 30000 && me && navigator.onLine) syncPlaylists();
  });
  setInterval(() => {
    if (loadedId && !audio.paused) LS.set('pos', { id: loadedId, t: audio.currentTime });
  }, 5000);

  // Media Session — điều khiển trên màn hình khoá / tai nghe
  function updateMediaSession() {
    if (!('mediaSession' in navigator)) return;
    const it = curItem();
    if (!it) return;
    const lt = localThumbs.get(it.id);
    navigator.mediaSession.metadata = new MediaMetadata({
      title: it.title || 'YT Nền', artist: it.channel || '', album: Q.from || 'YT Nền',
      artwork: lt ? [{ src: lt, sizes: '320x180', type: 'image/jpeg' }]
        : [{ src: ytThumb(it.id), sizes: '320x180', type: 'image/jpeg' }, { src: `https://i.ytimg.com/vi/${it.id}/hqdefault.jpg`, sizes: '480x360', type: 'image/jpeg' }],
    });
  }
  if ('mediaSession' in navigator) {
    const set = (a, f) => { try { navigator.mediaSession.setActionHandler(a, f); } catch {} };
    // điều khiển áp dụng cho video nếu video là thứ phát gần nhất, ngược lại cho trình phát nhạc
    const vidOn = () => lastActive === 'video' && V.id && !ytp && videoEl.currentSrc;
    const target = () => (vidOn() ? videoEl : audio);
    set('play', () => { if (vidOn()) { videoEl.play().catch(() => {}); return; } userWantsPlay = true; if (loadedId) audio.play().catch(() => {}); else togglePlay(); });
    set('pause', () => { if (vidOn()) { videoEl.pause(); return; } userWantsPlay = false; audio.pause(); });
    set('nexttrack', () => { if (!vidOn()) next(); });
    set('previoustrack', () => { if (!vidOn()) prev(); });
    set('seekto', (d) => { target().currentTime = d.seekTime; });
    set('seekbackward', (d) => { const t = target(); t.currentTime = Math.max(0, t.currentTime - (d.seekOffset || 10)); });
    set('seekforward', (d) => { const t = target(); t.currentTime = t.currentTime + (d.seekOffset || 10); });
  }

  // ---------- giao diện trình phát ----------
  function setSrcText(t) { $('#pSrc').textContent = t; }
  function setPlayIcons(playing) { $('#miniPlay').textContent = $('#pPlay').textContent = playing ? '⏸' : '▶'; }
  function updatePlayerUI() {
    const it = curItem();
    $('#mini').hidden = !it;
    document.body.classList.toggle('has-mini', !!it);
    if (!it) return;
    const img = thumbOf(it.id);
    $('#miniImg').src = img; $('#pImg').src = img;
    $('#miniTitle').textContent = $('#pTitle').textContent = it.title || 'Đang tải…';
    $('#miniSub').textContent = $('#pSub').textContent = it.channel || '';
    $('#pFrom').textContent = Q.from ? `Đang phát từ: ${Q.from} · ${Q.idx + 1}/${Q.items.length}` : `${Q.idx + 1}/${Q.items.length}`;
    $('#pShuffle').classList.toggle('on', shuffle);
    $('#pRepeat').classList.toggle('on', repeat !== 'off');
    $('#pRepeat').textContent = repeat === 'one' ? '🔂' : '🔁';
    setPlayIcons(!!loadedId && !audio.paused);
    if (!$('#pQueue').hidden) drawQueue();
    rerenderCurRow();
  }
  function rerenderCurRow() {
    if (curRoute.name !== 'playlist') return;
    const it = curItem();
    $$('#view .lrow').forEach((r) => r.classList.remove('cur'));
    const pl = playlists.find((p) => p.id === curRoute.parts[1]);
    if (!pl || !it || Q.from !== pl.name) return;
    const i = pl.items.findIndex((x) => x.id === it.id);
    const rows = $$('#view .list .lrow');
    if (rows[i]) rows[i].classList.add('cur');
  }
  function drawQueue() {
    const box = $('#pQueue');
    box.innerHTML = '<h3 style="margin:6px 0">Danh sách chờ</h3>';
    Q.items.forEach((v, i) => {
      const r = lrow(v, { onClick: () => { Q.idx = i; saveQ(); loadCurrent(true); }, cur: i === Q.idx, right: el('<span></span>') });
      if (localIds.has(v.id)) $('.vsub', r).textContent = '📥 ' + $('.vsub', r).textContent;
      box.append(r);
    });
  }
  $('#mini').onclick = (e) => { if (e.target.closest('button')) return; $('#player').hidden = false; updatePlayerUI(); tick(); };
  $('#pClose').onclick = () => { $('#player').hidden = true; };
  $('#miniPlay').onclick = $('#pPlay').onclick = togglePlay;
  $('#miniNext').onclick = $('#pNext').onclick = () => { prime(); next(); };
  $('#pPrev').onclick = () => { prime(); prev(); };
  $('#pShuffle').onclick = () => {
    shuffle = !shuffle; LS.set('shuffle', shuffle);
    if (shuffle && Q.items.length > 2) { // trộn các bài phía sau bài hiện tại
      const rest = Q.items.splice(Q.idx + 1);
      for (let i = rest.length - 1; i > 0; i--) { const j = Math.floor(Math.random() * (i + 1)); [rest[i], rest[j]] = [rest[j], rest[i]]; }
      Q.items.push(...rest); saveQ();
    }
    updatePlayerUI();
  };
  $('#pRepeat').onclick = () => { repeat = { off: 'all', all: 'one', one: 'off' }[repeat]; LS.set('repeat', repeat); updatePlayerUI(); toast({ off: 'Tắt lặp', all: 'Lặp danh sách', one: 'Lặp 1 bài' }[repeat]); };
  $('#pQueueBtn').onclick = () => { const q = $('#pQueue'); q.hidden = !q.hidden; if (!q.hidden) drawQueue(); };
  $('#pAdd').onclick = () => { const it = curItem(); if (it) addSheet(it); };
  $('#pVideo').onclick = () => {
    const it = curItem();
    if (!it) return;
    const t = Math.floor(audio.currentTime || 0);
    userWantsPlay = false; audio.pause();
    $('#player').hidden = true;
    location.hash = `#/watch/${it.id}?t=${t}`;
  };
  // thanh tua
  let seeking = false;
  const seek = $('#seek');
  seek.addEventListener('input', () => { seeking = true; const d = audioDur(); $('#tCur').textContent = fmtDur((seek.value / 1000) * d); });
  seek.addEventListener('change', () => { const d = audioDur(); if (d && loadedId) audio.currentTime = (seek.value / 1000) * d; seeking = false; });
  const audioDur = () => (isFinite(audio.duration) && audio.duration) || (curItem() && curItem().duration) || 0;
  function tick() {
    if (!loadedId) return;
    const d = audioDur(), t = audio.currentTime || 0;
    $('#miniBar').style.width = d ? `${(t / d) * 100}%` : '0';
    if ($('#player').hidden || seeking) return;
    seek.value = d ? Math.round((t / d) * 1000) : 0;
    $('#tCur').textContent = fmtDur(t); $('#tDur').textContent = fmtDur(d);
    try { if (navigator.mediaSession && navigator.mediaSession.setPositionState && d) navigator.mediaSession.setPositionState({ duration: d, position: Math.min(t, d), playbackRate: 1 }); } catch {}
  }
  setInterval(tick, 500);

  // ================================================================ mạng
  function onNet() {
    $('#offBadge').hidden = navigator.onLine;
    if (navigator.onLine && me && started) { syncPlaylists(); for (const [id, s] of dlState) if (s === 'error') dlState.delete(id); syncDownloads(); }
  }
  window.addEventListener('online', onNet);
  window.addEventListener('offline', onNet);
  onNet();

  if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js').catch(() => {});
  boot();
})();
