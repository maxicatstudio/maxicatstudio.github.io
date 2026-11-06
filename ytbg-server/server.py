"""
YT Nền — máy chủ cá nhân (chỉ dành cho tài khoản do admin cấp).

Phục vụ cả giao diện web (thư mục web/) lẫn API. Mọi API trừ /api/login đều cần đăng nhập.

Quản lý tài khoản bằng dòng lệnh (chạy trong thư mục này):
    python server.py adduser <tên> [--admin]     tạo tài khoản (hỏi mật khẩu)
    python server.py passwd <tên>                đổi mật khẩu
    python server.py deluser <tên>               xoá tài khoản
    python server.py users                       liệt kê tài khoản
Hoặc đăng nhập bằng tài khoản admin và vào mục "Quản trị" trên web.
"""
import asyncio
import getpass
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import shutil
import struct
import sys
import time
from pathlib import Path

from aiohttp import ClientError, ClientSession, ClientTimeout, web
import yt_dlp

HERE = Path(__file__).resolve().parent
WEB = HERE / "web"
DATA = HERE / "data"
CONFIG_PATH = HERE / "config.json"
USERS_PATH = DATA / "users.json"
SESS_PATH = DATA / "sessions.json"
HLS_DIR = HERE / "cache" / "hls"

ID_RE = re.compile(r"^[\w-]{11}$")
FMT_RE = re.compile(r"^[\w-]{1,16}$")
CHAN_RE = re.compile(r"^(UC[\w-]{22}|@[\w.\-]{2,60})$")
USER_RE = re.compile(r"^[a-z0-9_.-]{3,32}$")
PID_RE = re.compile(r"^[\w-]{4,16}$")
SEG_RE = re.compile(r"^(init\.mp4|seg\d{5}\.m4s)$")
QUALITIES = (360, 480, 720, 1080)

COOKIE = "ytbg_s"
SESSION_DAYS = 90
CHUNK = 8 * 1024 * 1024      # tải từ YouTube theo khúc 8 MB
INFO_TTL = 2 * 3600          # link YouTube hết hạn sau ~6 giờ
LIST_TTL = 30 * 60           # cache kết quả tìm kiếm / kênh
PAGE = 20
MAX_PLAYLISTS = 200
MAX_ITEMS = 3000
DEFAULT_TOPICS = ["Nhạc mới nhất", "Nhạc trẻ hot", "Lofi chill", "Nhạc Bolero"]

os.environ["PATH"] = str(HERE / "bin") + os.pathsep + os.environ.get("PATH", "")  # deno.exe, ffmpeg.exe
FFMPEG = shutil.which("ffmpeg")
INTERNAL = secrets.token_urlsafe(18)  # khoá cho ffmpeg đọc luồng qua chính máy chủ này (chỉ từ 127.0.0.1)
MEDIA_TTL = 12 * 3600
HLS_KEEP = 12 * 3600       # giữ video đã ghép 12 giờ
HLS_IDLE = 10 * 60         # dừng ghép nếu 10 phút không ai xem
HLS_MAX_JOBS = 4


def log(*a):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), *a, flush=True)


# ================================================================ lưu trữ JSON
def read_json(p, default):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return default


def write_json(p, obj):
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, p)


def load_config():
    cfg = read_json(CONFIG_PATH, {})
    changed = False
    for k, v in {"host": "127.0.0.1", "port": 8080, "cookies": "cookies.txt",
                 "secret": secrets.token_hex(32), "video_cache_gb": 5}.items():
        if k not in cfg:
            cfg[k] = v
            changed = True
    if changed:
        write_json(CONFIG_PATH, cfg)
    return cfg


CFG = load_config()


# ================================================================ tài khoản
_users = {"mtime": None, "data": {}}


def users():
    """Đọc lại users.json khi file thay đổi (vd. admin vừa thêm tài khoản bằng dòng lệnh)."""
    try:
        m = USERS_PATH.stat().st_mtime
    except FileNotFoundError:
        return {}
    if m != _users["mtime"]:
        _users["data"] = read_json(USERS_PATH, {})
        _users["mtime"] = m
    return _users["data"]


def save_users(d):
    write_json(USERS_PATH, d)
    _users["mtime"] = None


def hash_pw(pw, it=240_000):
    salt = secrets.token_bytes(16)
    h = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, it)
    return f"pbkdf2${it}${salt.hex()}${h.hex()}"


def check_pw(pw, stored):
    try:
        _, it, salt, h = stored.split("$")
        calc = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), int(it)).hex()
        return hmac.compare_digest(calc, h)
    except Exception:
        return False


def valid_pw(pw):
    return isinstance(pw, str) and 6 <= len(pw) <= 200


# ================================================================ phiên đăng nhập
_sessions = None  # sha256(token) -> {"u": user, "exp": ts}


def sessions():
    global _sessions
    if _sessions is None:
        _sessions = read_json(SESS_PATH, {})
        now = time.time()
        for k in [k for k, v in _sessions.items() if v.get("exp", 0) < now]:
            del _sessions[k]
    return _sessions


def tok_key(tok):
    return hashlib.sha256(tok.encode()).hexdigest()


def new_session(user):
    tok = secrets.token_urlsafe(32)
    sessions()[tok_key(tok)] = {"u": user, "exp": time.time() + SESSION_DAYS * 86400}
    write_json(SESS_PATH, sessions())
    return tok


def session_user(tok):
    if not tok:
        return None
    s = sessions().get(tok_key(tok))
    if not s or s["exp"] < time.time() or s["u"] not in users():
        return None
    return s["u"]


def drop_sessions(pred):
    ss = sessions()
    for k in [k for k, v in ss.items() if pred(k, v)]:
        del ss[k]
    write_json(SESS_PATH, ss)


_fails = {}  # ip -> [ts...]


def too_many_fails(ip):
    now = time.time()
    lst = [t for t in _fails.get(ip, []) if now - t < 900]
    _fails[ip] = lst
    return len(lst) >= 10


# ================================================================ hồ sơ người dùng
_plocks = {}


def prof_path(u):
    return DATA / "profiles" / f"{u}.json"


def load_prof(u):
    p = read_json(prof_path(u), None) or {}
    p.setdefault("playlists", [])
    p.setdefault("history", [])
    p.setdefault("topics", list(DEFAULT_TOPICS))
    return p


async def mutate_prof(u, fn):
    lock = _plocks.setdefault(u, asyncio.Lock())
    async with lock:
        p = load_prof(u)
        res = fn(p)
        write_json(prof_path(u), p)
        return res, p


def clean_item(d):
    """Thông tin một video gửi từ trình duyệt -> chỉ giữ các trường hợp lệ."""
    if not isinstance(d, dict) or not ID_RE.match(str(d.get("id", ""))):
        raise web.HTTPBadRequest(text="video không hợp lệ")
    cid = str(d.get("channelId") or "")
    dur = d.get("duration")
    return {
        "id": d["id"],
        "title": str(d.get("title") or "")[:300],
        "channel": str(d.get("channel") or "")[:200],
        "channelId": cid if CHAN_RE.match(cid) else "",
        "duration": int(dur) if isinstance(dur, (int, float)) and dur > 0 else None,
    }


# ================================================================ yt-dlp
def ydl_opts(**extra):
    o = {"quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": True}
    cookies = HERE / str(CFG.get("cookies") or "")
    if CFG.get("cookies") and cookies.is_file():
        o["cookiefile"] = str(cookies)
    o.update(extra)
    return o


def flat_item(e, channel=None, channel_id=None):
    vid = e.get("id")
    if not vid or not ID_RE.match(vid):
        return None
    cid = e.get("channel_id") or channel_id or ""
    return {
        "id": vid,
        "title": e.get("title") or "",
        "channel": e.get("channel") or e.get("uploader") or channel or "",
        "channelId": cid if CHAN_RE.match(cid) else "",
        "duration": int(e["duration"]) if e.get("duration") else None,
        "views": e.get("view_count"),
        "live": e.get("live_status") == "is_live",
    }


def extract_video(video_id):
    with yt_dlp.YoutubeDL(ydl_opts(format="bestaudio/best")) as y:
        info = y.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
    fmts = []
    for f in info.get("formats") or []:
        if f.get("vcodec") not in (None, "none") or f.get("acodec") in (None, "none"):
            continue
        if f.get("protocol") not in ("https", "http") or not f.get("url"):
            continue
        ext = f.get("ext")
        fmts.append({
            "id": str(f["format_id"]),
            "url": f["url"],
            "headers": f.get("http_headers") or {},
            "mime": "audio/mp4" if ext in ("m4a", "mp4") else "audio/webm" if ext == "webm" else f"audio/{ext}",
            "ext": "m4a" if ext in ("m4a", "mp4") else ext,
            "codec": f.get("acodec"),
            "bitrate": int((f.get("abr") or f.get("tbr") or 0) * 1000),
            "size": f.get("filesize") or None,
            "lang_pref": f.get("language_preference"),
        })
    if any((f["lang_pref"] or 0) >= 10 for f in fmts):  # bỏ bản lồng tiếng
        fmts = [f for f in fmts if (f["lang_pref"] or 0) >= 10]
    # Video H.264 (iPhone phát được): bản chỉ-hình (ghép với tiếng bằng ffmpeg) và bản đã ghép sẵn (360p)
    vids = []
    for f in info.get("formats") or []:
        vc, ac = f.get("vcodec") or "none", f.get("acodec") or "none"
        if not vc.startswith("avc1") or f.get("protocol") not in ("https", "http") or not f.get("url"):
            continue
        m = re.match(r"(\d{3,4})p", f.get("format_note") or "")
        q = int(m.group(1)) if m else (f.get("height") or 0)
        vids.append({
            "id": str(f["format_id"]), "url": f["url"], "headers": f.get("http_headers") or {},
            "mime": "video/mp4", "ext": "mp4", "q": q, "kind": "v" if ac == "none" else "muxed",
            "size": f.get("filesize") or None, "bitrate": int((f.get("tbr") or 0) * 1000),
            "codec": vc, "w": f.get("width"), "h": f.get("height"),
        })
    cid = info.get("channel_id") or ""
    return {
        "id": video_id,
        "title": info.get("title") or "",
        "channel": info.get("channel") or info.get("uploader") or "",
        "channelId": cid if CHAN_RE.match(cid) else "",
        "duration": info.get("duration"),
        "views": info.get("view_count"),
        "uploadDate": info.get("upload_date"),
        "description": (info.get("description") or "")[:5000],
        "live": info.get("live_status") in ("is_live", "is_upcoming"),
        "formats": fmts,
        "video": vids,
    }


def search_sync(q, n):
    with yt_dlp.YoutubeDL(ydl_opts(extract_flat="in_playlist")) as y:
        r = y.extract_info(f"ytsearch{n}:{q}", download=False)
    return [x for x in (flat_item(e) for e in r.get("entries") or []) if x]


def channel_sync(cid, n):
    url = f"https://www.youtube.com/channel/{cid}/videos" if cid.startswith("UC") else f"https://www.youtube.com/{cid}/videos"
    with yt_dlp.YoutubeDL(ydl_opts(extract_flat="in_playlist", playlistend=n)) as y:
        r = y.extract_info(url, download=False)
    name = r.get("channel") or r.get("uploader") or re.sub(r"\s*-\s*Videos$", "", r.get("title") or "")
    real_id = r.get("channel_id") or (cid if cid.startswith("UC") else "")
    items = [x for x in (flat_item(e, name, real_id) for e in r.get("entries") or []) if x]
    return {"name": name, "channelId": real_id, "items": items}


_cache = {}   # key -> (time, value)
_clocks = {}


async def cached(key, ttl, fn, *args, force=False):
    hit = _cache.get(key)
    if hit and not force and time.time() - hit[0] < ttl:
        return hit[1]
    lock = _clocks.setdefault(key, asyncio.Lock())
    async with lock:
        hit = _cache.get(key)
        if hit and not force and time.time() - hit[0] < ttl:
            return hit[1]
        t = time.time()
        val = await asyncio.get_running_loop().run_in_executor(None, fn, *args)
        log(f"{key[:60]} ({time.time() - t:.1f}s)")
        _cache[key] = (time.time(), val)
        if len(_cache) > 1500:
            for k, _ in sorted(_cache.items(), key=lambda kv: kv[1][0])[:300]:
                _cache.pop(k, None)
        return val


def get_info(vid, force=False):
    return cached("v:" + vid, INFO_TTL, extract_video, vid, force=force)


async def search(q, page):
    n = PAGE * page
    items = await cached(f"s:{n}:{q.lower()}", LIST_TTL, search_sync, q, n)
    return {"items": items[PAGE * (page - 1):], "nextpage": page + 1 if len(items) >= n and page < 5 else None}


async def channel(cid, page):
    n = 30 * page
    r = await cached(f"c:{n}:{cid}", LIST_TTL, channel_sync, cid, n)
    return {"name": r["name"], "channelId": r["channelId"], "items": r["items"][30 * (page - 1):],
            "nextpage": page + 1 if len(r["items"]) >= n and page < 10 else None}


# ================================================================ link media có chữ ký
# <audio>/<video>/HLS không phải lúc nào cũng gửi cookie (nhất là trên iPhone), nên link media
# mang theo chữ ký riêng: /m/<hết hạn>/<user>/<chữ ký>/...  (hết hạn sau 12 giờ)
def media_sig(user, vid, exp):
    return hmac.new(CFG["secret"].encode(), f"{exp}.{user}.{vid}".encode(), hashlib.sha256).hexdigest()[:32]


def media_prefix(user, vid):
    exp = (int(time.time()) // 3600 + 1) * 3600 + MEDIA_TTL  # làm tròn theo giờ -> link ổn định
    return f"/m/{exp}/{user}/{media_sig(user, vid, exp)}"


def check_media(request):
    mi = request.match_info
    try:
        exp = int(mi["exp"])
    except ValueError:
        raise web.HTTPForbidden()
    u, vid = mi["user"], mi["vid"]
    if (exp < time.time() or not USER_RE.match(u) or u not in users() or not ID_RE.match(vid)
            or not hmac.compare_digest(mi["sig"], media_sig(u, vid, exp))):
        raise web.HTTPForbidden(text="Link hết hạn — tải lại trang")


# ================================================================ HTTP: tiện ích
def is_https(request):
    return request.headers.get("X-Forwarded-Proto", request.scheme) == "https"


def client_ip(request):
    return (request.headers.get("X-Forwarded-For") or request.remote or "").split(",")[0].strip()


async def body_json(request):
    try:
        d = await request.json()
    except Exception:
        raise web.HTTPBadRequest(text="JSON không hợp lệ")
    if not isinstance(d, dict):
        raise web.HTTPBadRequest(text="JSON không hợp lệ")
    return d


def err(status, msg):
    return web.json_response({"error": msg}, status=status)


def need_admin(request):
    if not users().get(request["user"], {}).get("admin"):
        raise web.HTTPForbidden(text="Chỉ admin")


@web.middleware
async def mw(request, handler):
    path = request.path
    if path.startswith("/internal/"):
        if request.remote not in ("127.0.0.1", "::1") or request.match_info.get("secret") != INTERNAL:
            raise web.HTTPForbidden()
    elif path.startswith("/api/"):
        # chống CSRF: mọi request API phải có header riêng (trang khác không gửi được nếu không qua CORS)
        if request.headers.get("X-YTBG") != "1" and not path.startswith("/api/thumb/"):
            return err(403, "Thiếu header")
        if path != "/api/login":
            u = session_user(request.cookies.get(COOKIE))
            if not u:
                return err(401, "Cần đăng nhập")
            request["user"] = u
    try:
        return await handler(request)
    except web.HTTPException as e:
        if path.startswith("/api/") and e.status >= 400:
            return err(e.status, e.text if e.text and not e.text.startswith(str(e.status)) else e.reason)
        raise
    except yt_dlp.utils.DownloadError as e:
        msg = str(e).replace("\x1b", "")
        log("yt-dlp lỗi:", msg[:300])
        if "confirm you" in msg and "bot" in msg:
            msg = "YouTube chặn máy chủ (cần cookies.txt — xem README)"
        elif "unavailable" in msg.lower() or "private" in msg.lower():
            msg = "Video không xem được (bị xoá, riêng tư hoặc chặn theo vùng)"
        return err(502, msg[:300])


# ================================================================ HTTP: tài khoản
async def h_login(request):
    ip = client_ip(request)
    if too_many_fails(ip):
        return err(429, "Sai quá nhiều lần, thử lại sau 15 phút")
    d = await body_json(request)
    u = str(d.get("username", "")).strip().lower()
    rec = users().get(u)
    if not rec or not check_pw(str(d.get("password", "")), rec.get("pw", "")):
        _fails.setdefault(ip, []).append(time.time())
        await asyncio.sleep(1)
        return err(401, "Sai tên đăng nhập hoặc mật khẩu")
    _fails.pop(ip, None)
    resp = web.json_response({"user": u, "admin": bool(rec.get("admin"))})
    resp.set_cookie(COOKIE, new_session(u), max_age=SESSION_DAYS * 86400, httponly=True,
                    samesite="Lax", secure=is_https(request), path="/")
    log(f"đăng nhập: {u} ({ip})")
    return resp


async def h_logout(request):
    tok = request.cookies.get(COOKIE)
    if tok:
        drop_sessions(lambda k, v: k == tok_key(tok))
    resp = web.json_response({"ok": True})
    resp.del_cookie(COOKIE, path="/")
    return resp


async def h_me(request):
    u = request["user"]
    return web.json_response({"user": u, "admin": bool(users()[u].get("admin")), "yt_dlp": yt_dlp.version.__version__})


async def h_password(request):
    u = request["user"]
    d = await body_json(request)
    us = users()
    if not check_pw(str(d.get("old", "")), us[u]["pw"]):
        return err(400, "Mật khẩu cũ không đúng")
    if not valid_pw(d.get("new")):
        return err(400, "Mật khẩu mới cần 6 ký tự trở lên")
    us[u]["pw"] = hash_pw(d["new"])
    save_users(us)
    cur = tok_key(request.cookies.get(COOKIE, ""))
    drop_sessions(lambda k, v: v["u"] == u and k != cur)  # đăng xuất các máy khác
    return web.json_response({"ok": True})


async def h_admin_users(request):
    need_admin(request)
    return web.json_response({"users": [
        {"username": k, "admin": bool(v.get("admin")), "created": v.get("created")} for k, v in sorted(users().items())]})


async def h_admin_create(request):
    need_admin(request)
    d = await body_json(request)
    u = str(d.get("username", "")).strip().lower()
    if not USER_RE.match(u):
        return err(400, "Tên đăng nhập: 3–32 ký tự a-z, 0-9, dấu . _ -")
    if not valid_pw(d.get("password")):
        return err(400, "Mật khẩu cần 6 ký tự trở lên")
    us = users()
    if u in us:
        return err(400, "Tài khoản đã tồn tại")
    us[u] = {"pw": hash_pw(d["password"]), "admin": bool(d.get("admin")), "created": int(time.time())}
    save_users(us)
    log(f"admin {request['user']} tạo tài khoản {u}")
    return await h_admin_users(request)


async def h_admin_update(request):
    need_admin(request)
    u = request.match_info["u"]
    us = users()
    if u not in us:
        raise web.HTTPNotFound(text="Không có tài khoản")
    d = await body_json(request)
    if "password" in d:
        if not valid_pw(d["password"]):
            return err(400, "Mật khẩu cần 6 ký tự trở lên")
        us[u]["pw"] = hash_pw(d["password"])
        drop_sessions(lambda k, v: v["u"] == u)
    if "admin" in d:
        if u == request["user"] and not d["admin"]:
            return err(400, "Không tự bỏ quyền admin của chính mình")
        us[u]["admin"] = bool(d["admin"])
    save_users(us)
    return await h_admin_users(request)


async def h_admin_delete(request):
    need_admin(request)
    u = request.match_info["u"]
    if u == request["user"]:
        return err(400, "Không tự xoá chính mình")
    us = users()
    if us.pop(u, None) is None:
        raise web.HTTPNotFound(text="Không có tài khoản")
    save_users(us)
    drop_sessions(lambda k, v: v["u"] == u)
    log(f"admin {request['user']} xoá tài khoản {u}")
    return await h_admin_users(request)


# ================================================================ HTTP: xem video
def page_of(request, hi):
    try:
        return max(1, min(hi, int(request.query.get("page", "1"))))
    except ValueError:
        raise web.HTTPBadRequest()


async def h_search(request):
    q = request.query.get("q", "").strip()[:200]
    if not q:
        raise web.HTTPBadRequest(text="Thiếu từ khoá")
    return web.json_response(await search(q, page_of(request, 5)))


async def h_channel(request):
    cid = request.match_info["cid"]
    if not CHAN_RE.match(cid):
        raise web.HTTPBadRequest(text="Kênh không hợp lệ")
    return web.json_response(await channel(cid, page_of(request, 10)))


def public_info(info, user):
    out = {k: v for k, v in info.items() if k not in ("formats", "video")}
    pre = media_prefix(user, info["id"])
    out["audio"] = [{"url": f"{pre}/a/{info['id']}/{f['id']}", "mime": f["mime"], "ext": f["ext"],
                     "codec": f["codec"], "bitrate": f["bitrate"]} for f in info["formats"]]
    return out


async def h_video(request):
    vid = request.match_info["vid"]
    if not ID_RE.match(vid):
        raise web.HTTPBadRequest(text="ID video không hợp lệ")
    return web.json_response(public_info(await get_info(vid), request["user"]))


def title_query(t):
    t = re.sub(r"[\(\[\{【].*?[\)\]\}】]", " ", t)
    t = re.sub(r"(official|music video|mv|lyrics?|video|audio|hd|4k)", " ", t, flags=re.I)
    words = [w for w in re.split(r"[\s|\-–—:,/]+", t) if w]
    return " ".join(words[:6])


async def h_related(request):
    vid = request.match_info["vid"]
    if not ID_RE.match(vid):
        raise web.HTTPBadRequest()
    info = await get_info(vid)
    jobs = []
    if info["channelId"]:
        jobs.append(channel(info["channelId"], 1))
    q = title_query(info["title"]) or info["channel"]
    if q:
        jobs.append(search(q, 1))
    res = await asyncio.gather(*jobs, return_exceptions=True)
    lists = [r["items"] for r in res if isinstance(r, dict)]
    out, seen = [], {vid}
    for i in range(max((len(x) for x in lists), default=0)):  # xen kẽ: cùng kênh / tương tự
        for lst in reversed(lists):
            if i < len(lst) and lst[i]["id"] not in seen:
                seen.add(lst[i]["id"])
                out.append(lst[i])
    return web.json_response({"items": out[:30]})


async def h_home(request):
    p = load_prof(request["user"])
    chans = []
    for h in p["history"]:
        if h.get("channelId") and all(c[0] != h["channelId"] for c in chans):
            chans.append((h["channelId"], h.get("channel") or ""))
        if len(chans) >= 3:
            break
    jobs = [channel(cid, 1) for cid, _ in chans] + [search(t, 1) for t in p["topics"][:8]]
    res = await asyncio.gather(*jobs, return_exceptions=True)
    sections = []
    if p["history"]:
        sections.append({"title": "Xem gần đây", "kind": "history", "items": p["history"][:15]})
    for (cid, name), r in zip(chans, res[:len(chans)]):
        if isinstance(r, dict) and r["items"]:
            sections.append({"title": f"Mới từ {r['name'] or name}", "channelId": cid, "items": r["items"][:15]})
    for t, r in zip(p["topics"][:8], res[len(chans):]):
        if isinstance(r, dict) and r["items"]:
            sections.append({"title": t, "query": t, "items": r["items"][:15]})
        elif isinstance(r, Exception):
            log(f"home '{t}': {r}")
    return web.json_response({"topics": p["topics"], "sections": sections})


async def h_topics(request):
    d = await body_json(request)
    topics = [str(t).strip()[:60] for t in (d.get("topics") or []) if str(t).strip()][:12]

    def f(p):
        p["topics"] = topics
    await mutate_prof(request["user"], f)
    return web.json_response({"topics": topics})


async def h_history_get(request):
    return web.json_response({"items": load_prof(request["user"])["history"]})


async def h_history_add(request):
    it = clean_item(await body_json(request))

    def f(p):
        p["history"] = [it] + [h for h in p["history"] if h.get("id") != it["id"]][:199]
    await mutate_prof(request["user"], f)
    return web.json_response({"ok": True})


async def h_history_clear(request):
    def f(p):
        p["history"] = []
    await mutate_prof(request["user"], f)
    return web.json_response({"ok": True})


# ================================================================ HTTP: playlist
def pl_out(p):
    return web.json_response({"playlists": p["playlists"]})


def find_pl(p, pid):
    for pl in p["playlists"]:
        if pl["id"] == pid:
            return pl
    raise web.HTTPNotFound(text="Không có playlist")


def pl_name(d):
    name = str(d.get("name", "")).strip()[:100]
    if not name:
        raise web.HTTPBadRequest(text="Cần tên playlist")
    return name


async def h_pl_list(request):
    return pl_out(load_prof(request["user"]))


async def h_pl_create(request):
    d = await body_json(request)
    name = pl_name(d)
    first = clean_item(d["item"]) if d.get("item") else None

    def f(p):
        if len(p["playlists"]) >= MAX_PLAYLISTS:
            raise web.HTTPBadRequest(text="Quá nhiều playlist")
        now = int(time.time())
        pl = {"id": secrets.token_urlsafe(6), "name": name, "created": now, "updated": now,
              "items": [{**first, "added": now}] if first else []}
        p["playlists"].append(pl)
        return pl
    pl, p = await mutate_prof(request["user"], f)
    return web.json_response({"playlist": pl, "playlists": p["playlists"]})


async def h_pl_update(request):
    pid = request.match_info["pid"]
    d = await body_json(request)

    def f(p):
        pl = find_pl(p, pid)
        if "name" in d:
            pl["name"] = pl_name(d)
        if "order" in d:  # danh sách id theo thứ tự mới
            by = {it["id"]: it for it in pl["items"]}
            order = [i for i in d["order"] if i in by]
            pl["items"] = [by[i] for i in dict.fromkeys(order)] + [it for it in pl["items"] if it["id"] not in set(order)]
        pl["updated"] = int(time.time())
    _, p = await mutate_prof(request["user"], f)
    return pl_out(p)


async def h_pl_delete(request):
    pid = request.match_info["pid"]

    def f(p):
        find_pl(p, pid)
        p["playlists"] = [pl for pl in p["playlists"] if pl["id"] != pid]
    _, p = await mutate_prof(request["user"], f)
    return pl_out(p)


async def h_pl_add(request):
    pid = request.match_info["pid"]
    it = clean_item(await body_json(request))

    def f(p):
        pl = find_pl(p, pid)
        if not any(x["id"] == it["id"] for x in pl["items"]):
            if len(pl["items"]) >= MAX_ITEMS:
                raise web.HTTPBadRequest(text="Playlist đã đầy")
            pl["items"].append({**it, "added": int(time.time())})
            pl["updated"] = int(time.time())
    _, p = await mutate_prof(request["user"], f)
    return pl_out(p)


async def h_pl_remove(request):
    pid, vid = request.match_info["pid"], request.match_info["vid"]

    def f(p):
        pl = find_pl(p, pid)
        pl["items"] = [x for x in pl["items"] if x["id"] != vid]
        pl["updated"] = int(time.time())
    _, p = await mutate_prof(request["user"], f)
    return pl_out(p)


# ================================================================ HTTP: âm thanh & ảnh
async def upstream_size(sess, f):
    if f.get("size"):
        return f["size"]
    async with sess.get(f["url"], headers={**f["headers"], "Range": "bytes=0-0"}) as r:
        if r.status not in (200, 206):
            return None
        m = re.search(r"/(\d+)$", r.headers.get("Content-Range", ""))
        if m:
            f["size"] = int(m.group(1))
        elif r.headers.get("Content-Length") and r.status == 200:
            f["size"] = int(r.headers["Content-Length"])
        return f.get("size")


async def find_format(vid, fid, force=False):
    info = await get_info(vid, force=force)
    return next((f for f in info["formats"] + info["video"] if f["id"] == fid), None)


async def h_media(request):
    """Link có chữ ký: /m/.../a/<vid>/<fmt> (âm thanh) hoặc /v/ (video ghép sẵn)."""
    check_media(request)
    return await proxy_format(request, request.match_info["vid"], request.match_info["fid"])


async def h_internal(request):
    return await proxy_format(request, request.match_info["vid"], request.match_info["fid"])


async def proxy_format(request, vid, fid):
    """Chuyển tiếp một định dạng của YouTube (hỗ trợ Range/tua), tải từ YouTube theo khúc."""
    if not ID_RE.match(vid) or not FMT_RE.match(fid):
        raise web.HTTPBadRequest()
    sess = request.app["sess"]
    f = await find_format(vid, fid)
    total = await upstream_size(sess, f) if f else None
    if f and total is None:  # link hết hạn -> lấy lại
        f = await find_format(vid, fid, force=True)
        total = await upstream_size(sess, f) if f else None
    if not f or not total:
        raise web.HTTPBadGateway(text="Không lấy được luồng âm thanh")

    start, end, partial = 0, total - 1, False
    m = re.match(r"bytes=(\d*)-(\d*)", request.headers.get("Range", ""))
    if m:
        partial = True
        a, b = m.group(1), m.group(2)
        if a == "" and b:
            start = max(0, total - int(b))
        else:
            start = int(a or 0)
            if b:
                end = min(int(b), total - 1)
    if start >= total or start > end:
        return web.Response(status=416, headers={"Content-Range": f"bytes */{total}"})

    resp = web.StreamResponse(status=206 if partial else 200)
    resp.headers.update({"Content-Type": f["mime"], "Accept-Ranges": "bytes",
                         "Content-Length": str(end - start + 1), "Cache-Control": "private, max-age=3600"})
    if partial:
        resp.headers["Content-Range"] = f"bytes {start}-{end}/{total}"
    await resp.prepare(request)
    if request.method == "HEAD":
        return resp
    pos, retried = start, False
    try:
        while pos <= end:
            stop = min(pos + CHUNK - 1, end)
            async with sess.get(f["url"], headers={**f["headers"], "Range": f"bytes={pos}-{stop}"}) as up:
                if up.status in (403, 410) and not retried:
                    retried = True
                    f = await find_format(vid, fid, force=True) or f
                    continue
                if up.status not in (200, 206):
                    log(f"upstream {vid}/{fid} HTTP {up.status}")
                    break
                async for data in up.content.iter_chunked(64 * 1024):
                    await resp.write(data)
                    pos += len(data)
                    if pos > stop:
                        break
    except (ConnectionError, asyncio.CancelledError):
        pass  # người xem tua / chuyển bài -> trình duyệt tự ngắt
    except (ClientError, asyncio.TimeoutError) as e:
        log(f"upstream {vid}/{fid} đứt giữa chừng: {e!r}")
    return resp


async def h_thumb(request):
    vid = request.match_info["vid"]
    if not ID_RE.match(vid):
        raise web.HTTPBadRequest()
    async with request.app["sess"].get(f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg") as r:
        if r.status != 200:
            raise web.HTTPNotFound()
        body = await r.read()
    return web.Response(body=body, content_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})


# ================================================================ video: phát thẳng không cần ghép
# Bản chỉ-hình và chỉ-tiếng của YouTube là fMP4 đã chia đoạn, đầu file có mục lục (sidx) ghi vị trí từng đoạn.
# Đọc mục lục rồi tạo playlist HLS đầy đủ ngay: tua tới đâu trình phát chỉ xin đúng đoạn đó (qua /a/ và /v/).
# Không đọc được mục lục thì quay về cách ghép bằng ffmpeg ở dưới.
INDEX_MAX = 4 * 1024 * 1024  # mục lục video rất dài vẫn chỉ vài trăm KB
_mp4_index = {}


def parse_mp4_index(data):
    """Trả về {"init": số byte ftyp+moov, "segs": [(vị trí, cỡ, giây)...]}, số byte cần đọc thêm, hoặc None nếu không phù hợp."""
    p, moov = 0, False
    while p + 8 <= len(data):
        size, typ = struct.unpack(">I4s", data[p:p + 8])
        hdr = 8
        if size == 1:
            if p + 16 > len(data):
                return p + 16
            size, hdr = struct.unpack(">Q", data[p + 8:p + 16])[0], 16
        if size < hdr or typ in (b"moof", b"mdat"):
            return None  # không có mục lục trước phần dữ liệu
        if typ == b"moov":
            moov = True
        if typ != b"sidx":
            p += size
            continue
        if not moov:
            return None
        if p + size > len(data):
            return p + size
        x = data[p + hdr:p + size]
        scale = struct.unpack(">I", x[8:12])[0]
        if x[0] == 0:
            first, q = struct.unpack(">I", x[16:20])[0], 20
        else:
            first, q = struct.unpack(">Q", x[20:28])[0], 28
        count = struct.unpack(">H", x[q + 2:q + 4])[0]
        q += 4
        if not scale or not count or len(x) < q + 12 * count:
            return None
        pos, segs = p + size + first, []
        for _ in range(count):
            ref, dur, sap = struct.unpack(">III", x[q:q + 12])
            q += 12
            if ref >> 31 or not sap >> 31:  # mục lục lồng nhau / đoạn không mở đầu bằng keyframe
                return None
            segs.append((pos, ref & 0x7FFFFFFF, dur / scale))
            pos += ref & 0x7FFFFFFF
        return {"init": p, "segs": segs}
    return p + 8


async def load_mp4_index(sess, vid, f):
    key = (vid, f["id"])
    if key in _mp4_index:
        return _mp4_index[key]
    want = 32 * 1024
    while want <= INDEX_MAX:
        async with sess.get(f["url"], headers={**f["headers"], "Range": f"bytes=0-{want - 1}"}) as r:
            if r.status not in (200, 206):
                raise web.HTTPBadGateway(text=f"YouTube trả {r.status}")
            data = await r.content.read(want) if r.status == 200 else await r.read()
        res = parse_mp4_index(data)
        if res is None or isinstance(res, dict):
            if res:
                if len(_mp4_index) > 500:
                    _mp4_index.clear()
                _mp4_index[key] = res
            return res
        want = max(want * 2, res + 1024)
    return None


def direct_formats(info, q):
    vf = next((f for f in sorted(info["video"], key=lambda f: -f["bitrate"])
               if f["kind"] == "v" and f["q"] == q and f.get("codec")), None)
    af = next((f for f in sorted(info["formats"], key=lambda f: -f["bitrate"])
               if f["mime"] == "audio/mp4" and f.get("codec")), None)
    return (vf, af) if vf and af else None


async def direct_indexes(sess, vid, q, info):
    """Mục lục (hình, tiếng); link hết hạn thì lấy lại một lần. None nếu không phát thẳng được."""
    for force in (False, True):
        try:
            if force:
                info = await get_info(vid, force=True)
            pair = direct_formats(info, q)
            if not pair:
                return None
            vi, ai = await asyncio.gather(*(load_mp4_index(sess, vid, f) for f in pair))
            return (pair[0], vi, pair[1], ai) if vi and ai else None
        except (web.HTTPException, ClientError, asyncio.TimeoutError, struct.error) as e:
            log(f"mục lục {vid}_{q}: {e}")
    return None


def media_playlist(pre, kind, vid, f, idx):
    uri = f"{pre}/{kind}/{vid}/{f['id']}"
    lines = ["#EXTM3U", "#EXT-X-VERSION:7", f"#EXT-X-TARGETDURATION:{math.ceil(max(s[2] for s in idx['segs']))}",
             "#EXT-X-MEDIA-SEQUENCE:0", "#EXT-X-PLAYLIST-TYPE:VOD", "#EXT-X-INDEPENDENT-SEGMENTS",
             f'#EXT-X-MAP:URI="{uri}",BYTERANGE="{idx["init"]}@0"']
    for pos, size, dur in idx["segs"]:
        lines += [f"#EXTINF:{dur:.5f},", f"#EXT-X-BYTERANGE:{size}@{pos}", uri]
    return "\n".join(lines + ["#EXT-X-ENDLIST", ""])


def master_playlist(vf, vi, af, ai):
    rate = lambda idx: max(s[1] * 8 / s[2] for s in idx["segs"] if s[2] > 0)
    avg = lambda idx: sum(s[1] for s in idx["segs"]) * 8 / sum(s[2] for s in idx["segs"])
    res = f',RESOLUTION={vf["w"]}x{vf["h"]}' if vf.get("w") and vf.get("h") else ""
    return ("#EXTM3U\n#EXT-X-VERSION:7\n#EXT-X-INDEPENDENT-SEGMENTS\n"
            '#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="a",NAME="audio",DEFAULT=YES,AUTOSELECT=YES,URI="a.m3u8"\n'
            f'#EXT-X-STREAM-INF:BANDWIDTH={int(rate(vi) + rate(ai))},AVERAGE-BANDWIDTH={int(avg(vi) + avg(ai))},'
            f'CODECS="{vf["codec"]},{af["codec"]}"{res},AUDIO="a"\n'
            "v.m3u8\n")


async def h_direct(request):
    check_media(request)
    mi = request.match_info
    vid, name = mi["vid"], mi["name"]
    try:
        q = int(mi["q"])
    except ValueError:
        raise web.HTTPBadRequest()
    if q not in QUALITIES or name not in ("master.m3u8", "v.m3u8", "a.m3u8"):
        raise web.HTTPNotFound()
    got = await direct_indexes(request.app["sess"], vid, q, await get_info(vid))
    if not got:
        raise web.HTTPBadGateway(text="Không đọc được mục lục video")
    vf, vi, af, ai = got
    pre = f"/m/{mi['exp']}/{mi['user']}/{mi['sig']}"
    if name == "master.m3u8":
        txt = master_playlist(vf, vi, af, ai)
    elif name == "v.m3u8":
        txt = media_playlist(pre, "v", vid, vf, vi)
    else:
        txt = media_playlist(pre, "a", vid, af, ai)
    return web.Response(text=txt, content_type="application/vnd.apple.mpegurl", headers={"Cache-Control": "no-cache"})


# ================================================================ video: ghép 720p/1080p thành HLS bằng ffmpeg
# YouTube chỉ có sẵn bản ghép hình+tiếng ở 360p. Từ 480p trở lên hình và tiếng là hai luồng riêng,
# nên máy chủ dùng ffmpeg ghép lại (chỉ chép dữ liệu, không mã hoá lại -> rất nhẹ CPU) thành HLS,
# định dạng iPhone phát sẵn và dùng được "Hình trong hình". Video phát được sau vài giây
# trong lúc ffmpeg vẫn đang ghép phần sau.
_jobs = {}  # "vid_q" -> {"state": running|done|error, "proc", "last", "err"}


def video_choices(info):
    vs = [f for f in info["video"] if f["kind"] == "v" and f["q"] in QUALITIES]
    return sorted({f["q"] for f in vs}), vs


def pick_quality(qualities, want):
    lower = [q for q in qualities if q <= want]
    return max(lower) if lower else min(qualities)


def hls_ready(d):
    p = d / "index.m3u8"
    try:
        return p.read_text(encoding="utf-8")
    except (FileNotFoundError, PermissionError):
        return None


_hls_locks = {}


async def ensure_hls(vid, q, info=None):
    key = f"{vid}_{q}"
    job = _jobs.get(key)
    if job and job["state"] in ("running", "done"):  # đường nhanh, không cần khoá
        job["last"] = time.time()
        return job
    async with _hls_locks.setdefault(key, asyncio.Lock()):
        return await _ensure_hls(vid, q, key, info)


async def _ensure_hls(vid, q, key, info):
    d = HLS_DIR / key
    job = _jobs.get(key)
    if job and job["state"] in ("running", "done"):
        job["last"] = time.time()
        return job
    txt = hls_ready(d)
    if txt and "#EXT-X-ENDLIST" in txt:  # đã ghép xong từ trước
        job = _jobs[key] = {"state": "done", "last": time.time(), "err": ""}
        return job
    info = info or await get_info(vid)
    vf = next((f for f in sorted(info["video"], key=lambda f: -f["bitrate"]) if f["kind"] == "v" and f["q"] == q), None)
    auds = sorted(info["formats"], key=lambda f: (f["mime"] != "audio/mp4", -f["bitrate"]))
    if not vf or not auds:
        raise web.HTTPBadGateway(text="Không có định dạng video phù hợp")
    af = auds[0]
    running = sorted((j["last"], k) for k, j in _jobs.items() if j["state"] == "running")
    while len(running) >= HLS_MAX_JOBS:  # quá nhiều việc ghép cùng lúc -> dừng việc lâu không ai xem nhất
        stop_job(running.pop(0)[1])
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True, exist_ok=True)
    base = f"http://127.0.0.1:{int(CFG['port'])}/internal/{INTERNAL}/{vid}"
    cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
           "-i", f"{base}/{vf['id']}", "-i", f"{base}/{af['id']}",
           "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy",
           "-c:a", "copy" if af["mime"] == "audio/mp4" else "aac", *([] if af["mime"] == "audio/mp4" else ["-b:a", "160k"]),
           "-f", "hls", "-hls_time", "4", "-hls_list_size", "0", "-hls_playlist_type", "event",
           "-hls_segment_type", "fmp4", "-hls_fmp4_init_filename", "init.mp4",
           "-hls_flags", "independent_segments+temp_file", "-start_number", "0",
           "-hls_segment_filename", "seg%05d.m4s", "index.m3u8"]
    proc = await asyncio.create_subprocess_exec(*cmd, cwd=str(d), stdin=asyncio.subprocess.DEVNULL,
                                                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
    job = _jobs[key] = {"state": "running", "proc": proc, "last": time.time(), "err": "", "t0": time.time()}
    log(f"ghép video {key} (hình {vf['id']} + tiếng {af['id']})")
    asyncio.get_running_loop().create_task(watch_job(key, job, d))
    return job


async def watch_job(key, job, d):
    err = (await job["proc"].stderr.read()).decode("utf-8", "replace").strip()
    rc = await job["proc"].wait()
    if job["state"] != "running":
        return  # đã bị dừng
    if rc == 0:
        job["state"] = "done"
        log(f"ghép xong {key} ({time.time() - job['t0']:.0f}s)")
    else:
        job["state"], job["err"] = "error", (err[-300:] or f"ffmpeg lỗi {rc}")
        log(f"ghép lỗi {key}: {job['err']}")
        shutil.rmtree(d, ignore_errors=True)


def stop_job(key):
    job = _jobs.pop(key, None)
    if job and job["state"] == "running":
        job["state"] = "stopped"
        try:
            job["proc"].kill()
        except ProcessLookupError:
            pass
        shutil.rmtree(HLS_DIR / key, ignore_errors=True)
        log(f"dừng ghép {key} (không ai xem)")


def dir_size(d):
    return sum(f.stat().st_size for f in d.iterdir() if f.is_file())


async def janitor():
    """Dừng việc ghép không ai xem, xoá video cũ, giữ bộ nhớ đệm dưới giới hạn."""
    while True:
        await asyncio.sleep(60)
        try:
            now = time.time()
            for k, j in list(_jobs.items()):
                if j["state"] == "running" and now - j["last"] > HLS_IDLE:
                    stop_job(k)
                elif j["state"] in ("done", "error") and now - j["last"] > HLS_KEEP:
                    _jobs.pop(k, None)
            if not HLS_DIR.exists():
                continue
            dirs = []
            for d in HLS_DIR.iterdir():
                if not d.is_dir():
                    continue
                j = _jobs.get(d.name)
                if j and j["state"] == "running":
                    continue
                last = j["last"] if j else d.stat().st_mtime
                if now - last > HLS_KEEP:
                    shutil.rmtree(d, ignore_errors=True)
                else:
                    dirs.append((last, d, dir_size(d)))
            limit = float(CFG.get("video_cache_gb") or 5) * 1024 ** 3
            total = sum(x[2] for x in dirs)
            for last, d, size in sorted(dirs, key=lambda x: x[0]):
                if total <= limit:
                    break
                shutil.rmtree(d, ignore_errors=True)
                _jobs.pop(d.name, None)
                total -= size
        except Exception as e:  # không để dọn dẹp làm sập máy chủ
            log("janitor:", e)


async def h_play(request):
    """Trang xem hỏi cách phát video: HLS (ghép), mp4 360p có sẵn, hoặc trình phát YouTube."""
    vid = request.match_info["vid"]
    if not ID_RE.match(vid):
        raise web.HTTPBadRequest()
    try:
        want = int(request.query.get("q", "720"))
    except ValueError:
        want = 720
    info = await get_info(vid)
    if info.get("live"):
        return web.json_response({"type": "yt", "reason": "Video trực tiếp — dùng trình phát YouTube"})
    pre = media_prefix(request["user"], vid)
    split, _ = video_choices(info)                      # chất lượng cần ghép (hình + tiếng riêng)
    if not FFMPEG or not info["formats"]:
        split = []
    mux = max((f for f in info["video"] if f["kind"] == "muxed"), key=lambda f: f["q"], default=None)
    quals = sorted(set(split) | ({mux["q"]} if mux else set()))
    if not quals:
        return web.json_response({"type": "yt", "reason": "Không có định dạng phát trực tiếp"})
    q = pick_quality(quals, want)
    note = "" if FFMPEG else "Máy chủ chưa có ffmpeg — chỉ phát được 360p"
    if mux and (q == mux["q"] or q not in split):  # bản ghép sẵn (360p) -> khỏi cần ffmpeg
        return web.json_response({"type": "mp4", "url": f"{pre}/v/{vid}/{mux['id']}", "q": mux["q"], "qualities": quals, "note": note})
    if await direct_indexes(request.app["sess"], vid, q, info):  # phát thẳng, tua tới đâu cũng được ngay
        return web.json_response({"type": "hls", "url": f"{pre}/d/{vid}/{q}/master.m3u8", "q": q, "qualities": quals})
    log(f"phát thẳng không được, chuyển sang ghép {vid}_{q}")
    await ensure_hls(vid, q, info)
    return web.json_response({"type": "hls", "url": f"{pre}/hls/{vid}/{q}/index.m3u8", "q": q, "qualities": quals})


async def h_hls(request):
    check_media(request)
    vid, name = request.match_info["vid"], request.match_info["name"]
    try:
        q = int(request.match_info["q"])
    except ValueError:
        raise web.HTTPBadRequest()
    if q not in QUALITIES or not (name == "index.m3u8" or SEG_RE.match(name)):
        raise web.HTTPNotFound()
    if not FFMPEG:
        raise web.HTTPServiceUnavailable(text="Máy chủ chưa có ffmpeg")
    job = await ensure_hls(vid, q)
    d = HLS_DIR / f"{vid}_{q}"
    deadline = time.time() + (40 if name == "index.m3u8" else 20)
    while True:  # chờ ffmpeg ghép xong đoạn đầu / đoạn được yêu cầu
        if job["state"] == "error":
            raise web.HTTPBadGateway(text="Ghép video lỗi: " + job["err"][:200])
        if name == "index.m3u8":
            txt = hls_ready(d)
            if txt and "#EXTINF" in txt:
                return web.Response(text=txt, content_type="application/vnd.apple.mpegurl",
                                    headers={"Cache-Control": "no-cache"})
        elif (d / name).is_file():
            return web.FileResponse(d / name, headers={"Content-Type": "video/mp4", "Cache-Control": "private, max-age=3600"})
        if time.time() > deadline:
            raise web.HTTPGatewayTimeout(text="Máy chủ ghép video quá lâu")
        if job["state"] not in ("running", "done"):
            job = await ensure_hls(vid, q)
        await asyncio.sleep(0.3)


# ================================================================ giao diện web
STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
    "/sw.js": ("sw.js", "text/javascript; charset=utf-8"),
    "/manifest.webmanifest": ("manifest.webmanifest", "application/manifest+json"),
    "/icon.svg": ("icon.svg", "image/svg+xml"),
    "/vendor/hls.min.js": ("vendor/hls.min.js", "text/javascript; charset=utf-8"),
}


async def h_static(request):
    name, ctype = STATIC[request.path]
    return web.FileResponse(WEB / name, headers={"Content-Type": ctype, "Cache-Control": "no-cache",
                                                 "X-Content-Type-Options": "nosniff", "Referrer-Policy": "same-origin"})


async def on_startup(app):
    app["sess"] = ClientSession(timeout=ClientTimeout(total=None, sock_connect=15, sock_read=30))
    app["janitor"] = asyncio.get_running_loop().create_task(janitor())
    if not FFMPEG:
        log("Không tìm thấy ffmpeg — video chỉ phát được 360p (chạy lại install.ps1 để cài)")


async def on_cleanup(app):
    app["janitor"].cancel()
    for k in [k for k, j in _jobs.items() if j["state"] == "running"]:
        stop_job(k)
    await app["sess"].close()


def make_app():
    app = web.Application(middlewares=[mw], client_max_size=256 * 1024)
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    r = app.router
    for path in STATIC:
        r.add_get(path, h_static)
    r.add_post("/api/login", h_login)
    r.add_post("/api/logout", h_logout)
    r.add_get("/api/me", h_me)
    r.add_post("/api/password", h_password)
    r.add_get("/api/admin/users", h_admin_users)
    r.add_post("/api/admin/users", h_admin_create)
    r.add_patch("/api/admin/users/{u}", h_admin_update)
    r.add_delete("/api/admin/users/{u}", h_admin_delete)
    r.add_get("/api/home", h_home)
    r.add_put("/api/topics", h_topics)
    r.add_get("/api/search", h_search)
    r.add_get("/api/channel/{cid}", h_channel)
    r.add_get("/api/video/{vid}", h_video)
    r.add_get("/api/related/{vid}", h_related)
    r.add_get("/api/history", h_history_get)
    r.add_post("/api/history", h_history_add)
    r.add_delete("/api/history", h_history_clear)
    r.add_get("/api/playlists", h_pl_list)
    r.add_post("/api/playlists", h_pl_create)
    r.add_patch("/api/playlists/{pid}", h_pl_update)
    r.add_delete("/api/playlists/{pid}", h_pl_delete)
    r.add_post("/api/playlists/{pid}/items", h_pl_add)
    r.add_delete("/api/playlists/{pid}/items/{vid}", h_pl_remove)
    r.add_get("/api/play/{vid}", h_play)
    r.add_get("/m/{exp}/{user}/{sig}/a/{vid}/{fid}", h_media)
    r.add_get("/m/{exp}/{user}/{sig}/v/{vid}/{fid}", h_media)
    r.add_get("/m/{exp}/{user}/{sig}/hls/{vid}/{q}/{name}", h_hls)
    r.add_get("/m/{exp}/{user}/{sig}/d/{vid}/{q}/{name}", h_direct)
    r.add_get("/internal/{secret}/{vid}/{fid}", h_internal)
    r.add_get("/api/thumb/{vid}", h_thumb)
    return app


# ================================================================ dòng lệnh
def ask_pw():
    while True:
        a = getpass.getpass("Mật khẩu (≥ 6 ký tự): ")
        if not valid_pw(a):
            print("Mật khẩu quá ngắn.")
            continue
        if getpass.getpass("Nhập lại: ") == a:
            return a
        print("Hai lần nhập không khớp.")


def cli(argv):
    cmd = argv[0]
    us = users()
    if cmd == "users":
        if not us:
            print("(chưa có tài khoản)")
        for k, v in sorted(us.items()):
            print(f"{k:32} {'admin' if v.get('admin') else ''}")
        return 0
    if len(argv) < 2:
        print(__doc__)
        return 1
    u = argv[1].strip().lower()
    if cmd == "adduser":
        if not USER_RE.match(u):
            print("Tên đăng nhập: 3–32 ký tự a-z, 0-9, dấu . _ -")
            return 1
        if u in us:
            print("Tài khoản đã tồn tại (dùng 'passwd' để đổi mật khẩu).")
            return 1
        us[u] = {"pw": hash_pw(ask_pw()), "admin": "--admin" in argv, "created": int(time.time())}
        save_users(us)
        print(f"Đã tạo tài khoản {u}{' (admin)' if '--admin' in argv else ''}.")
    elif cmd == "passwd":
        if u not in us:
            print("Không có tài khoản này.")
            return 1
        us[u]["pw"] = hash_pw(ask_pw())
        save_users(us)
        drop_sessions(lambda k, v: v["u"] == u)
        print("Đã đổi mật khẩu.")
    elif cmd == "deluser":
        if us.pop(u, None) is None:
            print("Không có tài khoản này.")
            return 1
        save_users(us)
        drop_sessions(lambda k, v: v["u"] == u)
        print("Đã xoá.")
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] in ("adduser", "passwd", "deluser", "users"):
        sys.exit(cli(args))
    if "--log" in args:
        lp = Path(args[args.index("--log") + 1])
        lp.parent.mkdir(parents=True, exist_ok=True)
        sys.stdout = sys.stderr = open(lp, "a", encoding="utf-8", buffering=1)
    host, port = CFG["host"], int(CFG["port"])
    log(f"YT Nền server — yt-dlp {yt_dlp.version.__version__} — http://{host}:{port}")
    if not users():
        log("CHƯA CÓ TÀI KHOẢN NÀO. Tạo admin: python server.py adduser <tên> --admin")
    web.run_app(make_app(), host=host, port=port, print=None, access_log=None)
