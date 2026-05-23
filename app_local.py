#!/usr/bin/env python3
"""
Facebook Ads Viewer — site local
Installation : pip install flask requests
Lancement    : python3 app_local.py
Puis ouvre   : http://localhost:5000
"""

import json, re, threading, webbrowser
from datetime import datetime, timezone
from urllib.parse import urlparse
from flask import Flask, request, jsonify, Response
import requests as req

app = Flask(__name__)

# ── Scraping ──────────────────────────────────────────────────────────────────

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
}

def fetch_page(url, cookies_raw=None):
    s = req.Session()
    s.headers.update(HEADERS)
    if cookies_raw:
        for c in json.loads(cookies_raw):
            if c.get("name") and c.get("value") is not None:
                domain = c.get("domain", ".facebook.com")
                if not domain.startswith("."):
                    domain = "." + domain
                s.cookies.set(c["name"], str(c["value"]), domain=domain)
    r = s.get(url, timeout=25, allow_redirects=True)
    if r.status_code == 403 or "executeChallenge" in r.text:
        m = re.search(r"fetch\('(/__rd_verify_[^']+)'", r.text)
        if m:
            s.post("https://www.facebook.com" + m.group(1),
                   headers={"Origin":"https://www.facebook.com","Referer":url,"Content-Length":"0"}, timeout=10)
            r = s.get(url, timeout=25, allow_redirects=True)
    r.raise_for_status()
    return r.text

def parse_node(node):
    snap = node.get("snapshot") or {}
    body = ""
    b = snap.get("body") or {}
    if isinstance(b, dict):
        body = b.get("text") or ""
    elif isinstance(b, str):
        body = re.sub(r"<[^>]+>", " ", b).strip()
    if not body:
        for bs in snap.get("bodies") or []:
            t = (bs.get("markup") or {}).get("__html","") or (bs.get("body") or {}).get("text","")
            if t: body = re.sub(r"<[^>]+>"," ",t).strip(); break

    images, videos = [], []
    for img in snap.get("images") or []:
        u = isinstance(img,dict) and (img.get("original_image_url") or img.get("resized_image_url") or img.get("url") or "")
        if u: images.append(u)
    for vid in snap.get("videos") or []:
        u = isinstance(vid,dict) and (vid.get("video_hd_url") or vid.get("video_sd_url") or vid.get("url") or "")
        if u: videos.append(u)
    for card in snap.get("cards") or []:
        if not isinstance(card,dict): continue
        u = card.get("original_image_url") or card.get("resized_image_url") or ""
        if u: images.append(u)
        u = card.get("video_hd_url") or card.get("video_sd_url") or ""
        if u: videos.append(u)

    start_ts = node.get("start_date") or node.get("startDate")
    end_ts   = node.get("end_date") or node.get("endDate")
    start_date = days_running = None
    if start_ts:
        sd = datetime.fromtimestamp(int(start_ts), tz=timezone.utc)
        start_date = sd.strftime("%d %b %Y")
        ref = datetime.fromtimestamp(int(end_ts), tz=timezone.utc) if end_ts else datetime.now(tz=timezone.utc)
        days_running = max(0, (ref - sd).days)

    ad_id = str(node.get("ad_archive_id") or node.get("adArchiveID") or "")
    if not ad_id and not body and not images and not videos:
        return None
    return {
        "id": ad_id,
        "page_name": snap.get("page_name") or node.get("pageName") or "",
        "page_likes": snap.get("page_like_count"),
        "start_date": start_date, "days_running": days_running,
        "body": body, "title": snap.get("title") or "",
        "link_url": snap.get("link_url") or "",
        "cta_label": snap.get("cta_text") or "",
        "cta_type": snap.get("cta_type") or "",
        "display_format": snap.get("display_format") or "",
        "platforms": node.get("publisher_platform") or node.get("publisherPlatform") or [],
        "collation_count": node.get("collation_count") or 1,
        "images": images, "videos": videos,
    }

def walk(obj, out, seen):
    if isinstance(obj, dict):
        if "ad_archive_id" in obj or "adArchiveID" in obj:
            ad = parse_node(obj)
            if ad and ad["id"] not in seen:
                seen.add(ad["id"]); out.append(ad)
        for v in obj.values(): walk(v, out, seen)
    elif isinstance(obj, list):
        for item in obj: walk(item, out, seen)

def scrape(url, cookies_raw=None):
    html = fetch_page(url, cookies_raw)
    ads, seen = [], set()
    for script in re.findall(r"<script[^>]*>(.*?)</script>", html, re.DOTALL):
        if "ad_archive_id" not in script: continue
        try: walk(json.loads(script), ads, seen)
        except:
            idx = script.find("{")
            if idx >= 0:
                try: walk(json.loads(script[idx:]), ads, seen)
                except: pass
    return ads

# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return HTML

@app.route("/scrape", methods=["POST"])
def api_scrape():
    body = request.get_json(force=True) or {}
    url  = body.get("url","").strip()
    cookies = body.get("cookies","").strip() or None
    if not url or "facebook.com/ads/library" not in url:
        return jsonify({"error": "Lien Facebook Ad Library invalide"}), 400
    try:
        ads = scrape(url, cookies)
        return jsonify({"ads": ads, "count": len(ads)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

ALLOWED = {"fbcdn.net","facebook.com","fbsbx.com","cdninstagram.com"}

@app.route("/dl")
def download():
    media_url = request.args.get("url","")
    filename  = request.args.get("name","media")
    if not media_url: return "Missing URL", 400
    host = urlparse(media_url).netloc
    if not any(host.endswith(d) for d in ALLOWED): return "Domaine non autorisé", 403
    try:
        r = req.get(media_url,
                    headers={"User-Agent": HEADERS["User-Agent"],
                             "Referer": "https://www.facebook.com/"},
                    stream=True, timeout=60)
        ct = r.headers.get("Content-Type","application/octet-stream")
        return Response(
            r.iter_content(8192),
            headers={"Content-Disposition": f'attachment; filename="{filename}"',
                     "Content-Type": ct}
        )
    except Exception as e:
        return str(e), 500

# ── HTML (page complète embarquée) ────────────────────────────────────────────

HTML = """<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Facebook Ads Viewer</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet">
<link href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.css" rel="stylesheet">
<style>
body{background:#f0f2f5;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif}
.hero{background:linear-gradient(135deg,#1877f2,#0a4f9e);padding:2rem 1rem 3.5rem;color:#fff}
.hero h1{font-weight:800;font-size:1.8rem}
.search-card{background:#fff;border-radius:16px;box-shadow:0 8px 32px rgba(0,0,0,.18);padding:1.5rem;margin-top:-2rem}
.btn-fb{background:#1877f2;color:#fff;border:none;border-radius:0 10px 10px 0;padding:.65rem 1.4rem;font-weight:700;font-size:.95rem}
.btn-fb:hover{background:#0c5fd1;color:#fff}
.btn-fb:disabled{opacity:.6}
#url-input{border:2px solid #e4e6ea;border-radius:10px 0 0 10px;padding:.65rem 1rem;font-size:.95rem;flex:1}
#url-input:focus{border-color:#1877f2;box-shadow:none;outline:none}
.ad-card{background:#fff;border-radius:14px;border:1px solid #dde0e6;overflow:hidden;height:100%;display:flex;flex-direction:column;transition:box-shadow .2s,transform .15s}
.ad-card:hover{box-shadow:0 6px 28px rgba(0,0,0,.12);transform:translateY(-2px)}
.ad-media video,.ad-media img{width:100%;max-height:250px;object-fit:cover;display:block;background:#111}
.ad-body{padding:1rem;display:flex;flex-direction:column;gap:.5rem;flex:1}
.dl-area{padding:.75rem 1rem;border-top:1px solid #dde0e6;background:#f9fafb;display:flex;flex-wrap:wrap;gap:.4rem}
.btn-dl{display:inline-flex;align-items:center;gap:.3rem;padding:.35rem .8rem;border-radius:8px;font-size:.8rem;font-weight:600;border:1.5px solid;text-decoration:none;cursor:pointer}
.btn-dl-img{border-color:#1877f2;color:#1877f2}.btn-dl-img:hover{background:#e8f1fd}
.btn-dl-vid{border-color:#e03131;color:#e03131}.btn-dl-vid:hover{background:#ffeaea}
.pill{display:inline-flex;align-items:center;gap:.25rem;padding:.2rem .5rem;border-radius:20px;font-size:.72rem;font-weight:500}
.pill-gray{background:#f0f2f5;color:#65676b}
.pill-green{background:#e6f9f3;color:#087251}
.pill-amber{background:#fff3e0;color:#b35c00}
.page-name{font-weight:700;font-size:.95rem}
.ad-copy{font-size:.88rem;line-height:1.55;white-space:pre-line;max-height:5.5em;overflow:hidden;transition:max-height .3s}
.ad-copy.expanded{max-height:2000px}
.toggle-copy{background:none;border:none;color:#1877f2;font-size:.8rem;font-weight:600;padding:0;cursor:pointer}
.spinner{width:52px;height:52px;border:5px solid #e4e6ea;border-top-color:#1877f2;border-radius:50%;animation:spin .9s linear infinite;margin:0 auto 1rem}
@keyframes spin{to{transform:rotate(360deg)}}
#loading{display:none;text-align:center;padding:3rem 1rem}
.state-box{text-align:center;padding:4rem 1rem;color:#65676b}
</style>
</head>
<body>

<div class="hero">
  <div class="container">
    <div class="row justify-content-center">
      <div class="col-lg-9 text-center">
        <h1><i class="bi bi-facebook me-2"></i>Facebook Ads Viewer</h1>
        <p style="opacity:.8">Colle le lien d'une bibliothèque d'annonces → toutes les pubs s'affichent avec téléchargement</p>
        <div class="search-card">
          <div class="d-flex">
            <input id="url" type="url" class="form-control" placeholder="https://www.facebook.com/ads/library/?active_status=active&view_all_page_id=...">
            <button class="btn-fb" id="btn" onclick="go()"><i class="bi bi-search me-1"></i>Analyser</button>
          </div>
          <details class="mt-3 text-start" style="font-size:.82rem">
            <summary class="text-muted" style="cursor:pointer"><i class="bi bi-cookie me-1"></i>Ajouter mes cookies Facebook (optionnel — pour voir plus de pubs)</summary>
            <div class="mt-2 p-2 rounded" style="background:#f0fff4;border:1px solid #b7ebc8">
              <small>1. Installe <a href="https://chromewebstore.google.com/detail/cookie-editor/hlkenndednhfkekhgcdicdfddnkalmdm" target="_blank">Cookie-Editor</a> · 2. Va sur facebook.com connecté · 3. Clique l'icône → Export as JSON · 4. Colle ici</small>
            </div>
            <textarea id="cookies" rows="3" class="form-control mt-2" style="font-size:.78rem;font-family:monospace" placeholder='[{"name":"c_user","value":"..."}]'></textarea>
          </details>
        </div>
      </div>
    </div>
  </div>
</div>

<div class="container py-4">
  <div id="loading">
    <div class="spinner"></div>
    <p class="fw-semibold">Analyse en cours…</p>
    <p class="text-muted small">Récupération des publicités ⚡</p>
  </div>
  <div id="err" class="alert alert-danger d-none"></div>
  <div id="header" class="d-none mb-3">
    <span class="fw-bold fs-5" id="cnt" style="color:#1877f2"></span>
    <span class="text-muted ms-1">publicité(s) trouvée(s)</span>
    <div class="small text-muted mt-1" id="note"></div>
  </div>
  <div id="empty" class="state-box d-none">
    <div style="font-size:3rem">🔍</div>
    <h5>Aucune publicité trouvée</h5>
    <p class="text-muted small">Vérifie que l'URL contient <code>view_all_page_id=</code></p>
  </div>
  <div id="grid" class="row g-3"></div>
</div>

<script>
async function go() {
  const url = document.getElementById('url').value.trim();
  if (!url) return;
  const cookies = document.getElementById('cookies').value.trim();

  document.getElementById('grid').innerHTML = '';
  document.getElementById('header').classList.add('d-none');
  document.getElementById('err').classList.add('d-none');
  document.getElementById('empty').classList.add('d-none');
  document.getElementById('loading').style.display = 'block';
  document.getElementById('btn').disabled = true;

  try {
    const r = await fetch('/scrape', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({url, cookies: cookies || undefined})
    });
    const d = await r.json();
    if (d.error) { showErr(d.error); return; }
    render(d.ads || []);
  } catch(e) {
    showErr('Erreur réseau : ' + e.message);
  } finally {
    document.getElementById('loading').style.display = 'none';
    document.getElementById('btn').disabled = false;
  }
}

function showErr(msg) {
  const el = document.getElementById('err');
  el.textContent = msg;
  el.classList.remove('d-none');
}

function esc(s) {
  return s ? String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;') : '';
}

function render(ads) {
  if (!ads.length) { document.getElementById('empty').classList.remove('d-none'); return; }
  document.getElementById('cnt').textContent = ads.length;
  document.getElementById('header').classList.remove('d-none');
  document.getElementById('note').textContent = 'Page : ' + (ads[0].page_name || '') + (ads[0].page_likes ? ' · ' + ads[0].page_likes.toLocaleString() + ' likes' : '');

  const grid = document.getElementById('grid');
  let html = '';
  for (let i = 0; i < ads.length; i++) {
    try { html += card(ads[i], i); } catch(e) { console.error(e); }
  }
  grid.innerHTML = html;
  document.getElementById('header').scrollIntoView({behavior:'smooth'});
}

function card(ad, i) {
  const hasVid = ad.videos && ad.videos.length;
  const hasImg = ad.images && ad.images.length;

  const media = hasVid
    ? `<div class="ad-media"><video controls preload="metadata" style="width:100%;max-height:250px;background:#111;display:block"><source src="${esc(ad.videos[0])}" type="video/mp4"></video></div>`
    : hasImg
    ? `<div class="ad-media"><img src="${esc(ad.images[0])}" onerror="this.closest('.ad-media').remove()"></div>`
    : '';

  const pills = `
    ${ad.start_date ? `<span class="pill pill-gray"><i class="bi bi-calendar3"></i> ${esc(ad.start_date)}</span>` : ''}
    ${ad.days_running != null ? `<span class="pill pill-amber"><i class="bi bi-clock"></i> ${ad.days_running}j</span>` : ''}
    ${ad.collation_count > 1 ? `<span class="pill pill-gray"><i class="bi bi-files"></i> ${ad.collation_count} créatifs</span>` : ''}
  `;

  const bodyHtml = ad.body ? `
    <div class="ad-copy" id="c${i}">${esc(ad.body)}</div>
    ${ad.body.length > 200 ? `<button class="toggle-copy" onclick="document.getElementById('c${i}').classList.toggle('expanded');this.textContent=document.getElementById('c${i}').classList.contains('expanded')?'Voir moins ▴':'Voir plus ▾'">Voir plus ▾</button>` : ''}
  ` : '';

  // Download buttons (via local proxy → téléchargement direct)
  let dlBtns = '';
  (ad.videos||[]).forEach((u,j) => {
    const name = `ad_${ad.id}_video${ad.videos.length>1?'_'+(j+1):''}.mp4`;
    dlBtns += `<a href="/dl?url=${encodeURIComponent(u)}&name=${name}" class="btn-dl btn-dl-vid" download="${name}"><i class="bi bi-download"></i> Vidéo${ad.videos.length>1?' '+(j+1):''}</a>`;
  });
  (ad.images||[]).forEach((u,j) => {
    const name = `ad_${ad.id}_image${ad.images.length>1?'_'+(j+1):''}.jpg`;
    dlBtns += `<a href="/dl?url=${encodeURIComponent(u)}&name=${name}" class="btn-dl btn-dl-img" download="${name}"><i class="bi bi-download"></i> Image${ad.images.length>1?' '+(j+1):''}</a>`;
  });

  return `
  <div class="col-sm-6 col-xl-4">
    <div class="ad-card">
      ${media}
      <div class="ad-body">
        <div class="d-flex justify-content-between align-items-start gap-2">
          <span class="page-name">${esc(ad.page_name||'Page inconnue')}</span>
          <span class="pill pill-green" style="flex-shrink:0"><i class="bi bi-circle-fill" style="font-size:.45rem"></i> Actif</span>
        </div>
        <div style="font-size:.7rem;font-family:monospace;background:#f0f2f5;padding:2px 6px;border-radius:4px;display:inline-block;width:fit-content">#${esc(ad.id)}</div>
        <div class="d-flex flex-wrap gap-1">${pills}</div>
        ${bodyHtml}
        ${ad.title ? `<div style="font-weight:700;font-size:.9rem">${esc(ad.title)}</div>` : ''}
        ${ad.link_url ? `<a href="${esc(ad.link_url)}" target="_blank" style="font-size:.78rem;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;display:block;color:#1877f2">${esc(ad.link_url)}</a>` : ''}
        ${ad.cta_label ? `<div><span style="display:inline-block;padding:.3rem .85rem;background:#1877f2;color:#fff;border-radius:6px;font-size:.82rem;font-weight:700">${esc(ad.cta_label)}</span></div>` : ''}
        <div style="flex:1"></div>
      </div>
      ${dlBtns ? `<div class="dl-area">${dlBtns}</div>` : ''}
    </div>
  </div>`;
}

document.getElementById('url').addEventListener('keydown', e => { if (e.key==='Enter') go(); });
</script>
</body>
</html>"""

# ── Démarrage ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 55)
    print("  Facebook Ads Viewer — site local")
    print("  Ouvre  http://localhost:5000  dans ton navigateur")
    print("  Ctrl+C pour arrêter")
    print("=" * 55)
    threading.Timer(1.5, lambda: webbrowser.open("http://localhost:5000")).start()
    app.run(host="127.0.0.1", port=5000, debug=False)
