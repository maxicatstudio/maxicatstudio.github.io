"""
YT Nền — máy chủ âm thanh tự host.

Trả dữ liệu theo dạng API của Piped (một phần), nên trang YT Nền dùng được ngay:
  GET /<key>/streams/<videoId>          -> thông tin bài + danh sách luồng âm thanh
  GET /<key>/search?q=...               -> tìm kiếm
  GET /<key>/nextpage/search?q=&nextpage=2
  GET /<key>/audio/<videoId>/<formatId> -> chuyển tiếp luồng âm thanh (hỗ trợ tua / Range)
  GET /<key>/                           -> kiểm tra máy chủ

Mọi đường dẫn đều phải có <key> (khoá bí mật trong config.json) để người lạ
không dùng ké VPS của bạn.
"""
import asyncio
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
CONFIG_PATH = HERE / "config.json"
ID_RE = re.compile(r"^[\w-]{11}$")
FMT_RE = re.compile(r"^[\w-]{1,16}$")
CHUNK = 8 * 1024 * 1024          # tải từ YouTube theo từng khúc 8 MB (tránh bị chặn/bóp tốc độ)
INFO_TTL = 2 * 3600              # link YouTube hết hạn sau ~6 giờ; làm mới sau 2 giờ
SEARCH_PAGE = 20

# Thư mục bin/ (chứa deno.exe mà yt-dlp cần để giải mã chữ ký YouTube) được thêm vào PATH
os.environ["PATH"] = str(HERE / "bin") + os.pathsep + os.environ.get("PATH", "")


# ---------------------------------------------------------------- cấu hình
def load_config():
    cfg = {}
    if CONFIG_PATH.exists():
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))  # utf-8-sig: file do PowerShell ghi có BOM
    changed = False
    defaults = {"key": secrets.token_urlsafe(12), "host": "127.0.0.1", "port": 8080, "cookies": "cookies.txt", "public_url": ""}
    for k, v in defaults.items():
        if k not in cfg:
            cfg[k] = v
            changed = True
    if changed:
        CONFIG_PATH.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    return cfg


CFG = load_config()


def log(*a):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), *a, flush=True)


# ---------------------------------------------------------------- yt-dlp
def ydl_opts(**extra):
    o = {"quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": True}
    cookies = HERE / CFG.get("cookies", "")
    if CFG.get("cookies") and cookies.is_file():
        o["cookiefile"] = str(cookies)
    o.update(extra)
    return o


def extract_video(video_id):
    """Chạy trong thread. Trả về dict thông tin + các định dạng chỉ-âm-thanh."""
    with yt_dlp.YoutubeDL(ydl_opts(format="bestaudio/best")) as y:
        info = y.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
    fmts = []
    for f in info.get("formats") or []:
        if f.get("vcodec") not in (None, "none") or f.get("acodec") in (None, "none"):
            continue
        if f.get("protocol") not in ("https", "http") or not f.get("url"):
            continue
        ext = f.get("ext")
        mime = "audio/mp4" if ext in ("m4a", "mp4") else "audio/webm" if ext == "webm" else f"audio/{ext}"
        fmts.append({
            "id": str(f["format_id"]),
            "url": f["url"],
            "headers": f.get("http_headers") or {},
            "mime": mime,
            "codec": f.get("acodec"),
            "bitrate": int((f.get("abr") or f.get("tbr") or 0) * 1000),
            "size": f.get("filesize") or None,
            "lang_pref": f.get("language_preference"),
        })
    # Video có nhiều bản âm thanh (lồng tiếng): yt-dlp đánh dấu bản gốc bằng language_preference >= 10
    if any((f["lang_pref"] or 0) >= 10 for f in fmts):
        fmts = [f for f in fmts if (f["lang_pref"] or 0) >= 10]
    return {
        "title": info.get("title"),
        "uploader": info.get("channel") or info.get("uploader"),
        "duration": info.get("duration"),
        "formats": fmts,
    }


def search_sync(q, n):
    with yt_dlp.YoutubeDL(ydl_opts(extract_flat="in_playlist")) as y:
        r = y.extract_info(f"ytsearch{n}:{q}", download=False)
    out = []
    for e in r.get("entries") or []:
        vid = e.get("id")
        if not vid or not ID_RE.match(vid):
            continue
        out.append({
            "type": "stream",
            "url": f"/watch?v={vid}",
            "title": e.get("title"),
            "uploaderName": e.get("channel") or e.get("uploader"),
            "duration": int(e["duration"]) if e.get("duration") else -1,
            "thumbnail": f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg",
        })
    return out


_cache = {}          # video_id -> (time, info)
_locks = {}


async def get_info(video_id, force=False):
    hit = _cache.get(video_id)
    if hit and not force and time.time() - hit[0] < INFO_TTL:
        return hit[1]
    lock = _locks.setdefault(video_id, asyncio.Lock())
    async with lock:
        hit = _cache.get(video_id)
        if hit and not force and time.time() - hit[0] < INFO_TTL:
            return hit[1]
        t = time.time()
        info = await asyncio.get_running_loop().run_in_executor(None, extract_video, video_id)
        log(f"extract {video_id}: {len(info['formats'])} luồng âm thanh, {time.time() - t:.1f}s")
        _cache[video_id] = (time.time(), info)
        if len(_cache) > 500:  # dọn bớt
            for k, _ in sorted(_cache.items(), key=lambda kv: kv[1][0])[:100]:
                _cache.pop(k, None)
        return info


# ---------------------------------------------------------------- HTTP
def base_url(request):
    if CFG.get("public_url"):
        return CFG["public_url"].rstrip("/")
    proto = request.headers.get("X-Forwarded-Proto", request.scheme)
    host = request.headers.get("X-Forwarded-Host", request.host)
    return f"{proto}://{host}"


@web.middleware
async def mw(request, handler):
    try:
        return await handler(request)
    except web.HTTPException as e:
        return web.Response(status=e.status, text=e.text or e.reason, headers={k: v for k, v in e.headers.items() if k.lower() == "content-range"})
    except yt_dlp.utils.DownloadError as e:
        msg = str(e).replace("\x1b", "")
        log("yt-dlp lỗi:", msg[:300])
        return web.json_response({"error": msg[:500]}, status=502)


async def add_cors(request, resp):
    # chạy ngay trước khi gửi header -> áp dụng cho cả luồng âm thanh
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Expose-Headers"] = "Content-Length, Content-Range, Accept-Ranges"


def check(request):
    if not secrets.compare_digest(request.match_info.get("key", ""), CFG["key"]):
        raise web.HTTPNotFound()


async def h_root(request):
    return web.Response(text="ok")


async def h_status(request):
    check(request)
    return web.json_response({"ok": True, "yt_dlp": yt_dlp.version.__version__, "cached": len(_cache)})


async def h_streams(request):
    check(request)
    vid = request.match_info["vid"]
    if not ID_RE.match(vid):
        raise web.HTTPBadRequest()
    info = await get_info(vid)
    base = f"{base_url(request)}/{CFG['key']}/audio/{vid}"
    return web.json_response({
        "title": info["title"],
        "uploader": info["uploader"],
        "duration": info["duration"],
        "thumbnailUrl": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
        "audioStreams": [
            {"url": f"{base}/{f['id']}", "itag": f["id"], "mimeType": f["mime"], "codec": f["codec"],
             "bitrate": f["bitrate"], "quality": f"{f['bitrate'] // 1000} kbps"}
            for f in info["formats"]
        ],
    })


async def do_search(q, page):
    n = SEARCH_PAGE * page
    items = await asyncio.get_running_loop().run_in_executor(None, search_sync, q, n)
    chunk = items[SEARCH_PAGE * (page - 1):]
    return web.json_response({"items": chunk, "nextpage": str(page + 1) if len(items) >= n and page < 5 else None})


async def h_search(request):
    check(request)
    q = request.query.get("q", "").strip()
    if not q:
        raise web.HTTPBadRequest()
    return await do_search(q, 1)


async def h_nextpage(request):
    check(request)
    q = request.query.get("q", "").strip()
    try:
        page = max(2, min(5, int(request.query.get("nextpage", "2"))))
    except ValueError:
        raise web.HTTPBadRequest()
    return await do_search(q, page)


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
    for f in info["formats"]:
        if f["id"] == fid:
            return f
    return None


async def h_audio(request):
    check(request)
    vid, fid = request.match_info["vid"], request.match_info["fid"]
    if not ID_RE.match(vid) or not FMT_RE.match(fid):
        raise web.HTTPBadRequest()
    sess = request.app["sess"]

    f = await find_format(vid, fid)
    total = await upstream_size(sess, f) if f else None
    if f and total is None:  # link có thể đã hết hạn -> lấy lại
        f = await find_format(vid, fid, force=True)
        total = await upstream_size(sess, f) if f else None
    if not f or not total:
        raise web.HTTPBadGateway(text="Không lấy được luồng âm thanh")

    start, end, partial = 0, total - 1, False
    rng = request.headers.get("Range")
    if rng:
        m = re.match(r"bytes=(\d*)-(\d*)", rng)
        if m:
            partial = True
            a, b = m.group(1), m.group(2)
            if a == "" and b:            # bytes=-500 (500 byte cuối)
                start = max(0, total - int(b))
            else:
                start = int(a or 0)
                if b:
                    end = min(int(b), total - 1)
    if start >= total or start > end:
        return web.Response(status=416, headers={"Content-Range": f"bytes */{total}"})

    resp = web.StreamResponse(status=206 if partial else 200)
    resp.headers.update({
        "Content-Type": f["mime"],
        "Accept-Ranges": "bytes",
        "Content-Length": str(end - start + 1),
        "Cache-Control": "private, max-age=3600",
    })
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
        pass  # người nghe tua / chuyển bài -> trình duyệt tự ngắt
    return resp


async def on_startup(app):
    app["sess"] = ClientSession(timeout=ClientTimeout(total=None, sock_connect=15, sock_read=30))


async def on_cleanup(app):
    await app["sess"].close()


def make_app():
    app = web.Application(middlewares=[mw])
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    app.on_response_prepare.append(add_cors)
    app.router.add_get("/", h_root)
    app.router.add_get("/{key}/", h_status)
    app.router.add_get("/{key}/streams/{vid}", h_streams)
    app.router.add_get("/{key}/search", h_search)
    app.router.add_get("/{key}/nextpage/search", h_nextpage)
    app.router.add_get("/{key}/audio/{vid}/{fid}", h_audio)
    return app


if __name__ == "__main__":
    # --log <file>: ghi log ra file (khi chạy nền bằng Task Scheduler)
    if "--log" in sys.argv:
        lp = Path(sys.argv[sys.argv.index("--log") + 1])
        lp.parent.mkdir(parents=True, exist_ok=True)
        sys.stdout = sys.stderr = open(lp, "a", encoding="utf-8", buffering=1)
    host, port = CFG["host"], int(CFG["port"])
    pub = CFG.get("public_url") or f"http://{host}:{port}"
    log(f"YT Nền server — yt-dlp {yt_dlp.version.__version__}")
    log(f"Nghe tại http://{host}:{port}")
    log(f"Địa chỉ dán vào app (mục Máy chủ riêng): {pub.rstrip('/')}/{CFG['key']}")
    web.run_app(make_app(), host=host, port=port, print=None, access_log=None)
