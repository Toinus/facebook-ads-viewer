#!/usr/bin/env python3
"""
Facebook Ads Scraper — usage local
pip install requests
python3 fb_ads.py
"""

import json, re, sys, webbrowser, os, time
from datetime import datetime, timezone
import requests

# ── Config ────────────────────────────────────────────────────────────────────
URL = sys.argv[1] if len(sys.argv) > 1 else input(
    "Colle le lien Facebook Ad Library ici :\n> "
).strip()

# ── Scraping (challenge bypass) ───────────────────────────────────────────────
print("\n⏳  Récupération en cours…")

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
})

resp = session.get(URL, timeout=20, allow_redirects=True)
if resp.status_code == 403 or "executeChallenge" in resp.text:
    m = re.search(r"fetch\('(/__rd_verify_[^']+)'", resp.text)
    if m:
        session.post(
            "https://www.facebook.com" + m.group(1),
            headers={"Origin": "https://www.facebook.com", "Referer": URL, "Content-Length": "0"},
            timeout=10,
        )
        resp = session.get(URL, timeout=20, allow_redirects=True)

if "ad_archive_id" not in resp.text:
    print("❌  Facebook n'a pas renvoyé de données publicitaires.")
    print("    Essaie d'utiliser le mode Cookies (voir plus bas).")
    sys.exit(1)

# ── Parsing ───────────────────────────────────────────────────────────────────
def parse_node(node):
    snap = node.get("snapshot") or {}
    body = ""
    b = snap.get("body") or {}
    if isinstance(b, dict):
        body = b.get("text") or ""
        if not body:
            raw = (b.get("markup") or {}).get("__html", "") if isinstance(b.get("markup"), dict) else ""
            body = re.sub(r"<[^>]+>", " ", raw).strip()
    elif isinstance(b, str):
        body = re.sub(r"<[^>]+>", " ", b).strip()

    images, videos = [], []
    for img in snap.get("images") or []:
        u = isinstance(img, dict) and (img.get("original_image_url") or img.get("resized_image_url") or img.get("url") or "")
        if u: images.append(u)
    for vid in snap.get("videos") or []:
        u = isinstance(vid, dict) and (vid.get("video_hd_url") or vid.get("video_sd_url") or vid.get("url") or "")
        if u: videos.append(u)
    for card in snap.get("cards") or []:
        if not isinstance(card, dict): continue
        u = card.get("original_image_url") or card.get("resized_image_url") or ""
        if u: images.append(u)
        u = card.get("video_hd_url") or card.get("video_sd_url") or ""
        if u: videos.append(u)

    start_ts = node.get("start_date") or node.get("startDate")
    end_ts   = node.get("end_date")   or node.get("endDate")
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
        "id":            ad_id,
        "page_name":     snap.get("page_name") or node.get("pageName") or "",
        "start_date":    start_date,
        "days_running":  days_running,
        "body":          body,
        "title":         snap.get("title") or "",
        "link_url":      snap.get("link_url") or "",
        "cta_label":     snap.get("cta_text") or "",
        "display_format":snap.get("display_format") or "",
        "platforms":     node.get("publisher_platform") or node.get("publisherPlatform") or [],
        "collation_count": node.get("collation_count") or 1,
        "images":        images,
        "videos":        videos,
    }

def walk(obj, out, seen):
    if isinstance(obj, dict):
        if "ad_archive_id" in obj or "adArchiveID" in obj:
            ad = parse_node(obj)
            if ad and ad["id"] not in seen:
                seen.add(ad["id"])
                out.append(ad)
        for v in obj.values(): walk(v, out, seen)
    elif isinstance(obj, list):
        for item in obj: walk(item, out, seen)

ads, seen = [], set()
scripts = re.findall(r"<script[^>]*>(.*?)</script>", resp.text, re.DOTALL)
for script in scripts:
    if "ad_archive_id" not in script: continue
    try: walk(json.loads(script), ads, seen)
    except json.JSONDecodeError:
        idx = script.find("{")
        if idx >= 0:
            try: walk(json.loads(script[idx:]), ads, seen)
            except: pass

if not ads:
    print("⚠️  Page récupérée mais aucune pub trouvée.")
    sys.exit(1)

print(f"✅  {len(ads)} pubs trouvées")

# ── HTML generation ───────────────────────────────────────────────────────────
def esc(s):
    if not s: return ""
    return str(s).replace("&","&amp;").replace("<","&lt;").replace(">","&gt;").replace('"',"&quot;")

PLATFORM_ICONS = {
    "facebook":"🔵","instagram":"📸","messenger":"💬",
    "audience_network":"🌐","threads":"🧵","whatsapp":"💚",
}

def card(ad, i):
    media = ""
    if ad["videos"]:
        media = f'<video controls preload="metadata" style="width:100%;max-height:240px;background:#111;display:block"><source src="{esc(ad["videos"][0])}" type="video/mp4"></video>'
    elif ad["images"]:
        media = f'<img src="{esc(ad["images"][0])}" style="width:100%;max-height:240px;object-fit:cover;display:block" onerror="this.remove()">'

    platforms = " ".join(
        PLATFORM_ICONS.get(p.lower(), "🌐") + " " + p.capitalize()
        for p in ad["platforms"][:4]
    )

    days = f'<span class="badge bg-warning text-dark">{ad["days_running"]} jours</span>' if ad["days_running"] is not None else ""

    body_html = ""
    if ad["body"]:
        short = esc(ad["body"][:250]) + ("…" if len(ad["body"]) > 250 else "")
        full  = esc(ad["body"])
        body_html = f'''<div id="body{i}" style="font-size:.88rem;white-space:pre-line;line-height:1.55">{short}</div>
        {"" if len(ad["body"]) <= 250 else f'<button onclick="var d=document.getElementById(\'body{i}\');d.innerHTML=\'{full.replace(chr(39), "&apos;")}\';this.remove()" style="border:none;background:none;color:#1877f2;font-size:.8rem;font-weight:600;padding:0;cursor:pointer">Voir plus ▾</button>'}'''

    # Download buttons
    dl_btns = ""
    for j, v in enumerate(ad["videos"]):
        dl_btns += f'<a href="{esc(v)}" target="_blank" class="btn btn-outline-danger btn-sm me-1 mb-1"><i>▶</i> Ouvrir vidéo {j+1 if len(ad["videos"])>1 else ""}</a>'
    for j, img in enumerate(ad["images"]):
        dl_btns += f'<a href="{esc(img)}" target="_blank" class="btn btn-outline-primary btn-sm me-1 mb-1">🖼 Ouvrir image {j+1 if len(ad["images"])>1 else ""}</a>'

    return f'''
<div class="col-sm-6 col-xl-4 mb-3">
  <div class="card h-100 shadow-sm">
    {media}
    <div class="card-body d-flex flex-column gap-2" style="font-size:.9rem">
      <div class="d-flex justify-content-between align-items-start gap-2">
        <strong style="font-size:.95rem">{esc(ad["page_name"] or "Page inconnue")}</strong>
        <span class="badge bg-success flex-shrink-0">Actif</span>
      </div>
      <div class="d-flex flex-wrap gap-1">
        {f'<code style="font-size:.7rem;background:#f0f2f5;padding:2px 6px;border-radius:4px">#{esc(ad["id"])}</code>' if ad["id"] else ""}
        {f'<span class="badge bg-light text-secondary border">📅 {esc(ad["start_date"])}</span>' if ad["start_date"] else ""}
        {days}
        {f'<span class="badge bg-light text-secondary border">🗂 {ad["collation_count"]} créatifs</span>' if ad["collation_count"] > 1 else ""}
      </div>
      {f'<div class="text-muted" style="font-size:.75rem">{platforms}</div>' if platforms else ""}
      {body_html}
      {f'<div style="font-weight:700;font-size:.9rem">{esc(ad["title"])}</div>' if ad["title"] else ""}
      {f'<a href="{esc(ad["link_url"])}" target="_blank" style="font-size:.78rem;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;display:block">{esc(ad["link_url"])}</a>' if ad["link_url"] else ""}
      {f'<div><span style="display:inline-block;padding:.3rem .8rem;background:#1877f2;color:white;border-radius:6px;font-size:.82rem;font-weight:700">{esc(ad["cta_label"])}</span></div>' if ad["cta_label"] else ""}
      <div class="mt-auto pt-2">
        {dl_btns if dl_btns else '<span class="text-muted" style="font-size:.78rem">Aucun média disponible</span>'}
      </div>
    </div>
  </div>
</div>'''

cards_html = "\n".join(card(ad, i) for i, ad in enumerate(ads))

page_url_short = URL[:80] + "…" if len(URL) > 80 else URL

html_output = f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Facebook Ads — {len(ads)} résultats</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet">
</head>
<body style="background:#f0f2f5">
<nav class="navbar navbar-dark" style="background:#1877f2">
  <div class="container">
    <span class="navbar-brand fw-bold">🔵 Facebook Ads Viewer</span>
    <span class="text-white-50" style="font-size:.85rem">Généré le {datetime.now().strftime("%d/%m/%Y à %H:%M")}</span>
  </div>
</nav>
<div class="container py-4">
  <div class="alert alert-success mb-4">
    <strong>{len(ads)} publicité(s) trouvée(s)</strong> —
    <a href="{esc(URL)}" target="_blank" style="font-size:.85rem">{esc(page_url_short)}</a>
    <div class="mt-1 text-muted" style="font-size:.8rem">
      💡 Pour télécharger une vidéo : clique "Ouvrir vidéo" puis fais clic droit → Enregistrer la vidéo sous
    </div>
  </div>
  <div class="row">
    {cards_html}
  </div>
</div>
</body>
</html>"""

# ── Save & open ───────────────────────────────────────────────────────────────
out_file = "resultats_ads.html"
with open(out_file, "w", encoding="utf-8") as f:
    f.write(html_output)

print(f"📄  Fichier généré : {os.path.abspath(out_file)}")
print("🌐  Ouverture dans le navigateur…")
webbrowser.open("file://" + os.path.abspath(out_file))
