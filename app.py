import json
import re
from datetime import datetime, timezone
from urllib.parse import urlparse, parse_qs

from flask import Flask, request, jsonify, render_template, Response
import requests as http_req
from playwright.sync_api import sync_playwright

app = Flask(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Parse a single ad node from Facebook's inline JSON
# ──────────────────────────────────────────────────────────────────────────────

def parse_node(node: dict) -> dict | None:
    snap = node.get('snapshot') or {}

    # body text (Facebook uses body.text in this API format)
    body = ''
    b = snap.get('body') or {}
    if isinstance(b, dict):
        body = b.get('text') or ''
        if not body:
            # fallback: markup format
            m = b.get('markup') or {}
            raw = m.get('__html', '') if isinstance(m, dict) else str(m)
            body = re.sub(r'<[^>]+>', ' ', raw).strip()
    elif isinstance(b, str):
        body = re.sub(r'<[^>]+>', ' ', b).strip()
    # try bodies array
    if not body:
        for bs in snap.get('bodies') or []:
            t = (bs.get('markup') or {}).get('__html', '') or (bs.get('body') or {}).get('text', '')
            if t:
                body = re.sub(r'<[^>]+>', ' ', t).strip()
                break

    # images
    images = []
    for img in snap.get('images') or []:
        if isinstance(img, dict):
            u = (img.get('original_image_url') or img.get('resized_image_url')
                 or img.get('url') or img.get('uri') or '')
            if u:
                images.append(u)
        elif isinstance(img, str) and img.startswith('http'):
            images.append(img)

    # videos
    videos = []
    for vid in snap.get('videos') or []:
        if isinstance(vid, dict):
            u = (vid.get('video_hd_url') or vid.get('video_sd_url')
                 or vid.get('url') or vid.get('uri') or '')
            if u:
                videos.append(u)
        elif isinstance(vid, str) and vid.startswith('http'):
            videos.append(vid)

    # carousel cards
    cards = []
    for card in snap.get('cards') or []:
        if not isinstance(card, dict):
            continue
        ci, cv = [], []
        u = card.get('original_image_url') or card.get('resized_image_url') or ''
        if u:
            ci.append(u)
            images.append(u)
        u = card.get('video_hd_url') or card.get('video_sd_url') or ''
        if u:
            cv.append(u)
            videos.append(u)
        cards.append({
            'title': card.get('title', ''),
            'body': (card.get('body') or {}).get('text', '') if isinstance(card.get('body'), dict) else str(card.get('body', '')),
            'link_url': card.get('link_url', ''),
            'cta_type': card.get('cta_type', ''),
            'images': ci,
            'videos': cv,
        })

    # dates — at node level (not inside snapshot)
    start_ts = node.get('start_date') or node.get('startDate')
    end_ts = node.get('end_date') or node.get('endDate')
    start_date = end_date = days_running = None
    if start_ts:
        sd = datetime.fromtimestamp(int(start_ts), tz=timezone.utc)
        start_date = sd.strftime('%d %b %Y')
        now = datetime.now(tz=timezone.utc)
        if end_ts:
            ed = datetime.fromtimestamp(int(end_ts), tz=timezone.utc)
            end_date = ed.strftime('%d %b %Y')
            days_running = max(0, (ed - sd).days)
        else:
            days_running = (now - sd).days

    # CTA — Facebook provides both cta_text (label) and cta_type (enum)
    cta_label = snap.get('cta_text') or ''
    cta_type  = snap.get('cta_type') or ''

    # identifiers & page info
    ad_id     = str(node.get('ad_archive_id') or node.get('adArchiveID') or '')
    page_name = snap.get('page_name') or node.get('pageName') or ''
    page_id   = str(node.get('page_id') or snap.get('page_id') or node.get('pageID') or '')

    # platforms
    platforms = node.get('publisher_platform') or node.get('publisherPlatform') or []

    # media type hint
    display_format = snap.get('display_format') or ''

    # extra page info
    page_like_count   = snap.get('page_like_count')
    page_categories   = snap.get('page_categories') or []
    page_profile_url  = snap.get('page_profile_picture_url') or ''
    page_profile_uri  = snap.get('page_profile_uri') or ''
    caption           = snap.get('caption') or ''

    if not ad_id and not body and not images and not videos:
        return None

    return {
        'id': ad_id,
        'page_name': page_name,
        'page_id': page_id,
        'page_like_count': page_like_count,
        'page_categories': page_categories,
        'page_profile_url': page_profile_url,
        'page_profile_uri': page_profile_uri,
        'status': 'ACTIVE' if node.get('is_active') else 'INACTIVE',
        'start_date': start_date,
        'end_date': end_date,
        'days_running': days_running,
        'body': body,
        'title': snap.get('title') or '',
        'link_description': snap.get('link_description') or '',
        'link_url': snap.get('link_url') or '',
        'caption': caption,
        'cta_type': cta_type,
        'cta_label': cta_label,
        'display_format': display_format,
        'images': images,
        'videos': videos,
        'cards': cards,
        'platforms': platforms,
        'collation_count': node.get('collation_count') or 1,
    }


def walk(obj, out: list, seen: set):
    """Recursively search any JSON structure for ad nodes."""
    if isinstance(obj, dict):
        if 'ad_archive_id' in obj or 'adArchiveID' in obj:
            ad = parse_node(obj)
            if ad and ad['id'] and ad['id'] not in seen:
                seen.add(ad['id'])
                out.append(ad)
        for v in obj.values():
            walk(v, out, seen)
    elif isinstance(obj, list):
        for item in obj:
            walk(item, out, seen)


def extract_from_scripts(scripts: list, out: list, seen: set):
    """Parse JSON from FB inline scripts and walk for ads."""
    for script in scripts:
        if not script or 'ad_archive_id' not in script:
            continue
        # Facebook scripts are complete JSON objects
        try:
            walk(json.loads(script), out, seen)
        except json.JSONDecodeError:
            # Some scripts might have a tiny prefix — try stripping to first {
            idx = script.find('{')
            if idx > 0:
                try:
                    walk(json.loads(script[idx:]), out, seen)
                except Exception:
                    pass


# ──────────────────────────────────────────────────────────────────────────────
# Cookie helpers
# ──────────────────────────────────────────────────────────────────────────────

def parse_cookies(raw: str) -> list:
    """Accept JSON array from Cookie-Editor / EditThisCookie / etc."""
    data = json.loads(raw)
    if not isinstance(data, list):
        raise ValueError("Les cookies doivent être un tableau JSON")

    SAME_SITE = {'Strict', 'Lax', 'None'}
    result = []
    for c in data:
        if not c.get('name') or c.get('value') is None:
            continue
        domain = c.get('domain', '.facebook.com')
        if not domain.startswith('.') and not domain.startswith('http'):
            domain = '.' + domain
        pw = {
            'name':   str(c['name']),
            'value':  str(c['value']),
            'domain': domain,
            'path':   c.get('path', '/'),
        }
        if c.get('httpOnly'):
            pw['httpOnly'] = True
        if c.get('secure'):
            pw['secure'] = True
        ss = c.get('sameSite', '')
        if ss in SAME_SITE:
            pw['sameSite'] = ss
        result.append(pw)
    return result


# ──────────────────────────────────────────────────────────────────────────────
# Playwright scraper — memory-optimised for 512 MB servers
# ──────────────────────────────────────────────────────────────────────────────

_CHROMIUM_ARGS = [
    '--no-sandbox',
    '--disable-setuid-sandbox',
    '--disable-dev-shm-usage',   # use /tmp instead of /dev/shm
    '--disable-gpu',
    '--no-zygote',
    '--single-process',          # one process instead of many → ~half the RAM
    '--disable-extensions',
    '--disable-background-networking',
    '--disable-default-apps',
    '--disable-sync',
    '--mute-audio',
    '--no-first-run',
    '--disable-accelerated-2d-canvas',
    '--disable-webgl',
    '--disable-software-rasterizer',
    '--disable-blink-features=AutomationControlled',
]


def _run_playwright(url: str, pw_cookies: list) -> list:
    ads: list = []
    seen: set = set()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=_CHROMIUM_ARGS)
        ctx = browser.new_context(
            user_agent=(
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'
            ),
            locale='en-US',
            viewport={'width': 1024, 'height': 768},
        )
        page = ctx.new_page()

        # Block images / video / audio / fonts — not needed for data, saves RAM
        def _block(route):
            if route.request.resource_type in ('image', 'media', 'font'):
                route.abort()
            else:
                route.continue_()

        page.route('**/*', _block)
        page.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
        )

        try:
            if pw_cookies:
                ctx.add_cookies(pw_cookies)

            # domcontentloaded is faster than networkidle (FB never reaches idle)
            page.goto(url, wait_until='domcontentloaded', timeout=35000)

            # Poll every second until ad data appears (handles JS challenge + reload)
            scripts = []
            for _ in range(25):
                scripts = page.evaluate(
                    "Array.from(document.querySelectorAll('script'))"
                    ".filter(s=>!s.src&&s.textContent.includes('ad_archive_id'))"
                    ".map(s=>s.textContent)"
                )
                if scripts:
                    break
                page.wait_for_timeout(1000)

            extract_from_scripts(scripts, ads, seen)
            print(f'[scrape] found {len(ads)} ads')

        except Exception as e:
            print(f'[scrape] error: {e}')
        finally:
            browser.close()

    return ads


def scrape(url: str) -> list:
    return _run_playwright(url, [])


def scrape_with_cookies(url: str, cookies_raw: str) -> list:
    return _run_playwright(url, parse_cookies(cookies_raw))


# ──────────────────────────────────────────────────────────────────────────────
# Official Facebook Ad Library API  (requires user access token)
# ──────────────────────────────────────────────────────────────────────────────

_API_FIELDS = ','.join([
    'id', 'ad_creation_time', 'ad_creative_bodies',
    'ad_creative_link_captions', 'ad_creative_link_descriptions',
    'ad_creative_link_titles', 'ad_delivery_start_time',
    'ad_delivery_stop_time', 'ad_snapshot_url',
    'page_id', 'page_name', 'publisher_platforms',
    'currency', 'impressions', 'spend',
])


def _parse_api_date(s: str):
    if not s:
        return None, None
    try:
        dt = datetime.fromisoformat(s.replace('Z', '+00:00'))
        return dt, dt.strftime('%d %b %Y')
    except Exception:
        return None, None


def parse_api_node(ad: dict) -> dict:
    bodies       = ad.get('ad_creative_bodies') or []
    titles       = ad.get('ad_creative_link_titles') or []
    descriptions = ad.get('ad_creative_link_descriptions') or []
    captions     = ad.get('ad_creative_link_captions') or []

    sd_obj, start_date = _parse_api_date(ad.get('ad_delivery_start_time') or '')
    ed_obj, end_date   = _parse_api_date(ad.get('ad_delivery_stop_time') or '')

    days_running = None
    if sd_obj:
        ref = ed_obj if ed_obj else datetime.now(tz=timezone.utc)
        days_running = max(0, (ref - sd_obj).days)

    impressions = ad.get('impressions') or {}
    spend       = ad.get('spend') or {}
    imp_str  = (f"{impressions.get('lower_bound','?')} – {impressions.get('upper_bound','?')}"
                if impressions else '')
    spend_str = (f"{spend.get('lower_bound','?')} – {spend.get('upper_bound','?')} {ad.get('currency','')}"
                 if spend else '')

    return {
        'id':               ad.get('id', ''),
        'page_name':        ad.get('page_name', ''),
        'page_id':          str(ad.get('page_id', '')),
        'page_like_count':  None,
        'page_categories':  [],
        'page_profile_url': '',
        'page_profile_uri': '',
        'status':           'ACTIVE',
        'start_date':       start_date,
        'end_date':         end_date,
        'days_running':     days_running,
        'body':             bodies[0] if bodies else '',
        'title':            titles[0] if titles else '',
        'link_description': descriptions[0] if descriptions else '',
        'link_url':         captions[0] if captions else '',
        'caption':          captions[0] if captions else '',
        'cta_type':         '',
        'cta_label':        '',
        'display_format':   '',
        'images':           [],
        'videos':           [],
        'cards':            [],
        'platforms':        ad.get('publisher_platforms') or [],
        'collation_count':  1,
        'snapshot_url':     ad.get('ad_snapshot_url', ''),
        'impressions_str':  imp_str,
        'spend_str':        spend_str,
        'source':           'api',
    }


def fetch_all_via_api(page_id: str, country: str, active_status: str, token: str) -> list:
    """Call the official Facebook Ad Library API and paginate through all results."""
    ads = []
    params = {
        'access_token':      token,
        'ad_reached_countries': country,
        'search_page_ids':   page_id,
        'ad_active_status':  active_status,
        'fields':            _API_FIELDS,
        'limit':             100,
    }
    api_url = 'https://graph.facebook.com/v21.0/ads_archive'

    while True:
        r = http_req.get(api_url, params=params, timeout=30)
        data = r.json()

        if 'error' in data:
            msg = data['error'].get('message', 'Erreur API Facebook')
            code = data['error'].get('code', '')
            raise Exception(f"Erreur API ({code}): {msg}")

        for ad in data.get('data') or []:
            ads.append(parse_api_node(ad))

        paging = data.get('paging') or {}
        after  = (paging.get('cursors') or {}).get('after')
        if not after or not paging.get('next'):
            break
        params['after'] = after
        print(f'[api] paginating… {len(ads)} ads so far')

    return ads


# ──────────────────────────────────────────────────────────────────────────────
# Flask routes
# ──────────────────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/scrape', methods=['POST'])
def api_scrape():
    body = request.get_json(force=True) or {}
    url = body.get('url', '').strip()
    if not url or 'facebook.com/ads/library' not in url:
        return jsonify({'error': 'Lien Facebook Ad Library invalide'}), 400
    try:
        ads = scrape(url)
        return jsonify({'ads': ads, 'count': len(ads)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/scrape-cookies', methods=['POST'])
def api_scrape_cookies():
    body = request.get_json(force=True) or {}
    url         = body.get('url', '').strip()
    cookies_raw = body.get('cookies', '').strip()

    if not url or 'facebook.com/ads/library' not in url:
        return jsonify({'error': 'Lien Facebook Ad Library invalide'}), 400
    if not cookies_raw:
        return jsonify({'error': 'Cookies manquants'}), 400
    try:
        ads = scrape_with_cookies(url, cookies_raw)
        return jsonify({'ads': ads, 'count': len(ads), 'source': 'cookies'})
    except json.JSONDecodeError:
        return jsonify({'error': 'Format de cookies invalide. Assure-toi d\'exporter en JSON depuis Cookie-Editor.'}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/scrape-official', methods=['POST'])
def api_scrape_official():
    body = request.get_json(force=True) or {}
    url   = body.get('url', '').strip()
    token = body.get('token', '').strip()

    if not token:
        return jsonify({'error': 'Token Facebook manquant'}), 400
    if not url or 'facebook.com/ads/library' not in url:
        return jsonify({'error': 'Lien Facebook Ad Library invalide'}), 400

    # Parse params from URL
    parsed = urlparse(url)
    qs     = parse_qs(parsed.query)
    page_id = (qs.get('view_all_page_id') or [None])[0]
    country = (qs.get('country') or ['US'])[0].upper()
    raw_status = (qs.get('active_status') or ['active'])[0].lower()
    active_status = 'ACTIVE' if raw_status == 'active' else 'ALL'

    if not page_id:
        return jsonify({
            'error': 'Impossible de trouver le Page ID dans l\'URL. '
                     'Assure-toi que l\'URL contient view_all_page_id=...'
        }), 400

    try:
        ads = fetch_all_via_api(page_id, country, active_status, token)
        return jsonify({'ads': ads, 'count': len(ads), 'source': 'api'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


ALLOWED_CDN = {'fbcdn.net', 'facebook.com', 'fbsbx.com', 'cdninstagram.com'}


@app.route('/api/download')
def api_download():
    media_url = request.args.get('url', '')
    filename  = request.args.get('filename', 'media')
    if not media_url:
        return 'Missing URL', 400
    parsed = urlparse(media_url)
    if not any(parsed.netloc.endswith(d) for d in ALLOWED_CDN):
        return 'Unauthorized domain', 403
    try:
        r = http_req.get(
            media_url,
            headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://www.facebook.com/'},
            stream=True, timeout=60,
        )
        def generate():
            for chunk in r.iter_content(8192):
                if chunk:
                    yield chunk
        return Response(generate(), headers={
            'Content-Disposition': f'attachment; filename="{filename}"',
            'Content-Type': r.headers.get('Content-Type', 'application/octet-stream'),
        })
    except Exception as e:
        return str(e), 500


if __name__ == '__main__':
    import os
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
