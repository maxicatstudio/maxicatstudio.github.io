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
import os
import re
import secrets
import sys
import time
from pathlib import Path

from aiohttp import ClientSession, ClientTimeout, web
import yt_dlp

HERE = Path(__file__).resolve().parent
WEB = HERE / "web"
DATA = HERE / "data"
CONFIG_PATH = HERE / "config.json"
USERS_PATH = DATA / "users.json"
SESS_PATH = DATA / "sessions.json"

ID_RE = re.compile(r"^[\w-]{11}$")
FMT_RE = re.compile(r"^[\w-]{1,16}$")
CHAN_RE = re.compile(r"^(UC[\w-]{22}|@[\w.\-]{2,60})$")
USER_RE = re.compile(r"^[a-z0-9_.-]{3,32}$")
PID_RE = re.compile(r"^[\w-]{4,16}$")

COOKIE = "ytbg_s"
SESSION_DAYS = 90
CHUNK = 8 * 1024 * 1024      # tải từ YouTube theo khúc 8 MB
INFO_TTL = 2 * 3600          # link YouTube hết hạn sau ~6 giờ
LIST_TTL = 30 * 60           # cache kết quả tìm kiếm / kênh
PAGE = 20
MAX_PLAYLISTS = 200
MAX_ITEMS = 3000
DEFAULT_TOPICS = ["Nhạc mới nhất", "Nhạc trẻ hot", "Lofi chill", "Nhạc Bolero"]

os.environ["PATH"] = str(HERE / "bin") + os.pathsep + os.environ.get("PATH", "")  # deno.exe cho yt-dlp


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
    for k, v in {"host": "127.0.0.1", "port": 8080, "cookies": "cookies.txt"}.items():
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
        "formats": fmts,
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
    if path.startswith("/api/"):
        # chống CSRF: mọi request API phải có header riêng (trang khác không gửi được nếu không qua CORS)
        if request.headers.get("X-YTBG") != "1" and not path.startswith(("/api/audio/", "/api/thumb/")):
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


def public_info(info):
    out = {k: v for k, v in info.items() if k != "formats"}
    out["audio"] = [{"url": f"/api/audio/{info['id']}/{f['id']}", "mime": f["mime"], "ext": f["ext"],
                     "codec": f["codec"], "bitrate": f["bitrate"]} for f in info["formats"]]
    return out


async def h_video(request):
    vid = request.match_info["vid"]
    if not ID_RE.match(vid):
        raise web.HTTPBadRequest(text="ID video không hợp lệ")
    return web.json_response(public_info(await get_info(vid)))


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
    return next((f for f in info["formats"] if f["id"] == fid), None)


async def h_audio(request):
    vid, fid = request.match_info["vid"], request.match_info["fid"]
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
    except (ConnectionResetError, asyncio.CancelledError):
        pass  # người nghe tua / chuyển bài
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


# ================================================================ giao diện web
STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
    "/sw.js": ("sw.js", "text/javascript; charset=utf-8"),
    "/manifest.webmanifest": ("manifest.webmanifest", "application/manifest+json"),
    "/icon.svg": ("icon.svg", "image/svg+xml"),
}


async def h_static(request):
    name, ctype = STATIC[request.path]
    return web.FileResponse(WEB / name, headers={"Content-Type": ctype, "Cache-Control": "no-cache",
                                                 "X-Content-Type-Options": "nosniff", "Referrer-Policy": "same-origin"})


async def on_startup(app):
    app["sess"] = ClientSession(timeout=ClientTimeout(total=None, sock_connect=15, sock_read=30))


async def on_cleanup(app):
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
    r.add_get("/api/audio/{vid}/{fid}", h_audio)
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
