import json, logging, os, random, threading, uuid
import time
from flask import Flask, Response, jsonify, redirect, request, send_from_directory
from yca.urlparser import parse_video_id
from yca.pipeline import analyze_video
from yca.detector import CommentPointDetector
from yca.generator import CommentGenerator, DuplicateCommentFilter
from yca.storage import HistoryManager
from yca.subtitles import SubtitleError
from yca import export, ytapi
from yca.llm import OllamaWriter
from yca.pipeline import merge_comments

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data"); os.makedirs(DATA, exist_ok=True)
logging.basicConfig(filename=os.path.join(DATA, "app.log"), level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", encoding="utf-8")
log = logging.getLogger("yca")

def load_config():
    try:
        with open(os.path.join(ROOT, "config.json"), encoding="utf-8") as f: return json.load(f)
    except Exception: return {}
CFG = load_config()

def get_writer():
    c = CFG.get("llm") or {}
    if not c.get("enabled", True): return None
    return OllamaWriter(c.get("url", "http://127.0.0.1:11434"), c.get("model", "qwen2.5:7b"), c.get("timeout", 90))
WRITER = get_writer()

app = Flask(__name__)
history = HistoryManager(DATA)
JOBS = {}
yt = ytapi.YouTubeClient(ROOT, DATA)
POSTED = os.path.join(DATA, "posted.json")
MIN_GAP = 30          # iki gönderi arası en az saniye (spam gibi görünmesin)
_last_post = [0.0]

def _posted():
    try:
        with open(POSTED, encoding="utf-8") as f: return json.load(f)
    except Exception: return {}

def _run(job, vid):
    try:
        det = CommentPointDetector(min_gap=CFG.get("min_gap_seconds", 20), min_raw=CFG.get("min_raw_score", 3.0))
        def prog(stage, p): job.update(stage=stage, progress=p)
        job["result"] = analyze_video(vid, DATA, prog, weights=CFG.get("weights"), detector=det, seed=random.randrange(2**31),
                                    crowd_fetcher=lambda v: yt.top_comments(v) if (yt.connected() or yt.has_key()) else [], writer=WRITER)
        job.update(stage="Bitti", progress=1)
        log.info("analiz tamam %s", vid)
    except Exception as e:
        log.exception("analiz hatası %s", vid)
        job["error"] = str(e) if e.__class__.__name__ in ("SubtitleError", "NoMomentsError") else "Beklenmeyen hata oluştu. data/app.log dosyasına bakın."
    job["done"] = True

@app.post("/api/analyze")
def analyze():
    vid = parse_video_id((request.get_json(silent=True) or {}).get("url"))
    if not vid: return jsonify(error="Geçerli bir YouTube bağlantısı girin (watch, youtu.be, shorts veya live)."), 400
    jid = uuid.uuid4().hex
    JOBS[jid] = job = {"vid": vid, "stage": "Başlıyor", "progress": 0, "done": False}
    threading.Thread(target=_run, args=(job, vid), daemon=True).start()
    return jsonify(job=jid, vid=vid)

@app.get("/api/status/<jid>")
def status(jid):
    j = JOBS.get(jid)
    return jsonify(j) if j else (jsonify(error="Analiz bulunamadı."), 404)

@app.post("/api/alternative")
def alternative():
    d = request.get_json(silent=True) or {}
    try: res = history.load(d.get("vid"))
    except ValueError: res = None
    i = d.get("index")
    if not res or not isinstance(i, int) or not 0 <= i < len(res["moments"]): return jsonify(error="Analiz bulunamadı."), 404
    used = [t for m in res["moments"] for t in m.get("used", [])]
    m = res["moments"][i]
    tpl = CommentGenerator(seed=random.randrange(2**31), dup=DuplicateCommentFilter(used), crowd=res.get("crowd")).generate(m)
    got = {}
    if WRITER and WRITER.status()[0]:
        try: got = WRITER.write(m, (res.get("meta") or {}).get("title", ""), res.get("crowd"), used)
        except Exception: log.exception("alternatif yazılamadı"); got = {}
    m["comments"], m["source"] = merge_comments(got, tpl)
    m["used"] = m.get("used", []) + list(m["comments"].values())
    history.save(res)
    return jsonify(comments=m["comments"])

@app.get("/api/engine")
def engine():
    if not WRITER: return jsonify(engine="şablon", note="Ollama kapalı (config.json).")
    ok, model, why = WRITER.status(force=True)
    return jsonify(engine="ollama:" + model if ok else "şablon", note=why)

@app.get("/api/history")
def hist_list(): return jsonify(history.list())

@app.get("/api/history/<vid>")
def hist_get(vid):
    try: r = history.load(vid)
    except ValueError: r = None
    return jsonify(r) if r else (jsonify(error="Kayıt yok."), 404)

@app.get("/api/export/<vid>/<fmt>")
def export_(vid, fmt):
    try: r = history.load(vid)
    except ValueError: r = None
    if not r or fmt not in ("txt", "csv"): return jsonify(error="Bulunamadı."), 404
    length = request.args.get("len", "uzun")
    body = export.to_txt(r["moments"], length) if fmt == "txt" else export.to_csv(r["moments"], length)
    mime = "text/plain" if fmt == "txt" else "text/csv"
    return Response(body, mimetype=mime + "; charset=utf-8", headers={"Content-Disposition": f"attachment; filename=yorumlar_{vid}.{fmt}"})

@app.get("/api/auth")
def auth_state():
    out = {"configured": yt.configured(), "connected": yt.connected(), "channel": "", "has_key": yt.has_key()}
    if out["connected"]:
        try: out["channel"] = yt.channel_title()
        except ytapi.YtError as e: out["error"] = str(e); out["connected"] = yt.connected()
    return jsonify(out)

@app.get("/auth/login")
def auth_login():
    try: return redirect(yt.login_url())
    except ytapi.YtError as e: return Response(str(e), status=400, mimetype="text/plain; charset=utf-8")

@app.get("/auth/callback")
def auth_callback():
    if request.args.get("error"): return redirect("/?auth=iptal")
    try: yt.handle_callback(request.args.get("code"), request.args.get("state"))
    except ytapi.YtError as e: return Response(str(e), status=400, mimetype="text/plain; charset=utf-8")
    return redirect("/?auth=ok")

@app.post("/api/setup")
def setup():
    """Arayüzden yapıştırılan Client ID/Secret ve/veya API anahtarını bu bilgisayara kaydeder."""
    d = request.get_json(silent=True) or {}
    cid, sec, key = (d.get("client_id") or "").strip(), (d.get("client_secret") or "").strip(), (d.get("api_key") or "").strip()
    if not (cid or sec or key): return jsonify(error="Boş bırakma: Client ID + Secret ve/veya API anahtarını yapıştır."), 400
    try:
        if cid or sec: yt.save_client(cid, sec)
        if key: yt.save_key(key)
    except ytapi.YtError as e: return jsonify(error=str(e)), 400
    return jsonify(ok=True)

@app.post("/auth/logout")
def auth_logout(): yt.logout(); return jsonify(ok=True)

@app.post("/api/post")
def post_comment():
    """Kullanıcının onayladığı metni kendi hesabından videoya yorum olarak atar."""
    d = request.get_json(silent=True) or {}
    vid, text = d.get("vid"), (d.get("text") or "").strip()
    try: history.load(vid)
    except ValueError: return jsonify(error="Geçersiz video."), 400
    if not text: return jsonify(error="Yorum boş."), 400
    if not yt.connected(): return jsonify(error="Önce YouTube hesabına bağlan."), 401
    done = _posted()
    if done.get(vid) and not d.get("force"):
        return jsonify(error="Bu videoya zaten yorum attın. Yine de atmak için onayla.", duplicate=True), 409
    wait = MIN_GAP - (time.time() - _last_post[0])
    if wait > 0: return jsonify(error=f"Çok hızlısın, {int(wait) + 1} sn sonra tekrar dene."), 429
    try: cid = yt.post_comment(vid, text)
    except ytapi.YtError as e:
        log.warning("yorum atılamadı %s: %s", vid, e); return jsonify(error=str(e)), 502
    _last_post[0] = time.time()
    done.setdefault(vid, []).append({"id": cid, "time": int(time.time()), "text": text})
    with open(POSTED, "w", encoding="utf-8") as f: json.dump(done, f, ensure_ascii=False)
    log.info("yorum atıldı %s %s", vid, cid)
    return jsonify(ok=True, id=cid, url=f"https://www.youtube.com/watch?v={vid}&lc={cid}")

# ---------------------------------------------------------------- Yorum analizi (Comment Intelligence)
import csv, io
from yca.insight import pipeline as ci, report as ci_report
CJOBS = {}

def _ci_run(job, vid, mt, rep):
    try:
        job["result"] = ci.run(vid, DATA, yt, max_threads=mt, include_replies=rep, progress=lambda s, p: job.update(stage=s, progress=p),
                               transcript_fetcher=lambda v: __import__("yca.subtitles", fromlist=["x"]).fetch_subtitles(v, os.path.join(DATA, "cache")))
        job["result"] = {"vid": vid}          # sonuç dosyadan okunur (büyük olabilir)
    except ytapi.YtError as e: job["error"] = str(e)
    except Exception:
        log.exception("yorum analizi hatası %s", vid); job["error"] = "Beklenmeyen hata oluştu. data/app.log dosyasına bakın."
    job["done"] = True

@app.get("/api/ci/video")
def ci_video():
    vid = parse_video_id(request.args.get("url"))
    if not vid: return jsonify(error="Geçerli bir YouTube bağlantısı girin."), 400
    try: return jsonify(yt.video_info(vid))
    except ytapi.YtError as e: return jsonify(error=str(e)), 502

@app.post("/api/ci/analyze")
def ci_analyze():
    d = request.get_json(silent=True) or {}; vid = parse_video_id(d.get("url"))
    if not vid: return jsonify(error="Geçerli bir YouTube bağlantısı girin."), 400
    if not (yt.connected() or yt.has_key()): return jsonify(error="Yorumları okumak için API anahtarı gerekli (ana sayfadaki ayar kutusu)."), 400
    mt = max(100, min(int(d.get("max_threads") or 1000), 10000)); jid = uuid.uuid4().hex
    CJOBS[jid] = job = {"vid": vid, "stage": "Başlıyor", "progress": 0, "done": False}
    threading.Thread(target=_ci_run, args=(job, vid, mt, bool(d.get("include_replies", True))), daemon=True).start()
    return jsonify(job=jid, vid=vid)

@app.get("/api/ci/status/<jid>")
def ci_status(jid):
    j = CJOBS.get(jid); return jsonify(j) if j else (jsonify(error="Bulunamadı."), 404)

def _ci_res(vid):
    try: return ci.load(DATA, vid)
    except Exception: return None

@app.get("/api/ci/result/<vid>")
def ci_result(vid):
    if not ci_ok(vid): return jsonify(error="Geçersiz video."), 400
    r = _ci_res(vid)
    if not r: return jsonify(error="Analiz yok."), 404
    r = {k: v for k, v in r.items() if k != "comments"}; return jsonify(r)

def ci_ok(vid): return bool(__import__("re").match(r"^[A-Za-z0-9_-]{11}$", vid or ""))

@app.get("/api/ci/report/<vid>")
def ci_report_(vid):
    r = _ci_res(vid) if ci_ok(vid) else None
    fmt = request.args.get("format", "kisa")
    if not r or fmt not in ci_report.FORMATS: return jsonify(error="Analiz ya da rapor biçimi bulunamadı."), 404
    seed = request.args.get("seed", type=int)
    rep = ci.make_report(r, fmt, seed if seed is not None else random.randrange(2**31))
    return jsonify(rep)

@app.get("/api/ci/export/<vid>/<fmt>")
def ci_export(vid, fmt):
    r = _ci_res(vid) if ci_ok(vid) else None
    if not r or fmt not in ("json", "csv"): return jsonify(error="Bulunamadı."), 404
    if fmt == "json": return Response(json.dumps(r, ensure_ascii=False), mimetype="application/json; charset=utf-8", headers={"Content-Disposition": f"attachment; filename=analiz_{vid}.json"})
    buf = io.StringIO(); w = csv.writer(buf); w.writerow(["id", "ust_yorum", "yazar", "begeni", "tarih", "duygu", "guven", "kategori", "etiketler", "etki", "alayci", "tekrar", "yorum"])
    for c in r["comments"]: w.writerow([c["id"], c["parent"] or "", c["author"], c["likes"], c["published"], c["label"], c["confidence"], c["category"], "|".join(c["tags"]), c["impact"], int(c["irony"]), int(c["dup"]), c["text"]])
    return Response("\ufeff" + buf.getvalue(), mimetype="text/csv; charset=utf-8", headers={"Content-Disposition": f"attachment; filename=analiz_{vid}.csv"})

@app.get("/analiz")
def ci_page(): return send_from_directory(os.path.join(ROOT, "static"), "analiz.html")

@app.get("/")
def index(): return send_from_directory(os.path.join(ROOT, "static"), "index.html")

# ---------------------------------------------------------------- Giveaway Assistant (bağımsız modül)
from yca.giveaway import routes as giveaway_routes
giveaway_routes.init(app, ROOT, DATA, yt, WRITER, interval=(CFG.get("giveaway") or {}).get("interval_seconds", 60),
                     autostart=(CFG.get("giveaway") or {}).get("enabled", True))

# ---------------------------------------------------------------- YouTube Channel Radar (bağımsız modül)
from yca.radar import routes as radar_routes
radar_routes.init(app, ROOT, DATA, yt, CFG.get("radar"))

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
