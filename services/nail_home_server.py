"""
nail_home_server.py - FastAPI 首页（替代 Gradio 版本）
自包含 HTML/CSS/JS，无外部 CDN 依赖，纯 AJAX 交互，适配手机端
"""
import sys, os, json, csv, threading

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from nail_database import dm

dm.initialize()
ALL_PRODUCTS = dm.product.read_all()
RAW_IMAGES_DIR = os.path.join(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")), "assets", "raw_images")
PORT = 7860
TRYNON_PORT = 7885
FAV_PORT = 7886

PRIMARY_TAGS = ["全部", "场合", "款式", "颜色", "长度", "甲型"]
TAG_CSV_MAP = {
    "场合": "scene_label",
    "款式": "style_label",
    "颜色": "color_label",
    "长度": "length_label",
    "甲型": "nail_shape_label",
}

def _build_sub_tags():
    m = {}
    for primary, field in TAG_CSV_MAP.items():
        tags = set()
        for p in ALL_PRODUCTS:
            for v in p.get(field, "").split(","):
                v = v.strip()
                if v:
                    tags.add(v)
        m[primary] = sorted(tags)
    return m

SUB_TAGS_MAP = _build_sub_tags()

def _img_url(iid):
    for ext in [".webp", ".jpg", ".jpeg", ".png"]:
        fname = f"img_{str(iid).zfill(3)}{ext}"
        if os.path.exists(os.path.join(RAW_IMAGES_DIR, fname)):
            return f"/raw_images/{fname}"
    return ""

def _filter(primary, sub=None):
    if primary == "全部" or primary not in TAG_CSV_MAP:
        return list(ALL_PRODUCTS)
    field = TAG_CSV_MAP[primary]
    if not sub:
        vals_all = set()
        for p in ALL_PRODUCTS:
            for v in p.get(field, "").split(","):
                v = v.strip()
                if v:
                    vals_all.add(v)
        if not vals_all:
            return list(ALL_PRODUCTS)
        result = []
        for p in ALL_PRODUCTS:
            pvals = [v.strip() for v in p.get(field, "").split(",")]
            if any(v in vals_all for v in pvals):
                result.append(p)
        if len(result) == len(ALL_PRODUCTS):
            return list(ALL_PRODUCTS)
        return result
    result = []
    for p in ALL_PRODUCTS:
        vals = [v.strip() for v in p.get(field, "").split(",")]
        if sub in vals:
            result.append(p)
    return result

def _build_card_html(products):
    cards = ""
    for idx, p in enumerate(products):
        iid = p["item_id"]
        name = p.get("item_name", "")
        scene = p.get("scene_label", "").replace(",", " · ")
        img = _img_url(iid)
        delay = min(idx * 0.04, 0.6)
        num = str(iid).zfill(3)
        cards += f"""<div class="product-card" style="animation-delay:{delay}s">
  <div class="product-card-img-box">
    <img src="{img}" alt="{name}" loading="lazy" data-num="{num}" onerror="tryImg(this)" />
  </div>
  <div class="product-card-info">
    <div class="product-card-name">{name}</div>
    <div class="product-card-info-row">
      <div class="product-card-scene">{scene}</div>
      <button class="tryon-btn" onclick="_tryon({iid})">试戴</button>
    </div>
  </div>
</div>"""
    return f'<div class="card-wrap">{cards}</div>'

def _build_primary_html(active, tags=None):
    if tags is None:
        tags = PRIMARY_TAGS
    html = '<div class="brand-title">NAIL</div>'
    html += '<div class="tag-row">'
    for tag in tags:
        cls = " active" if tag == active else ""
        html += f'<span class="primary-tag{cls}" onclick="_tc(\'{tag}|\')">{tag}</span>'
    html += "</div>"
    return html

def _build_sub_html(primary, active_sub=None):
    if primary == "全部":
        return ""
    tags = SUB_TAGS_MAP.get(primary, [])
    if not tags:
        return ""
    html = '<div class="sub-row">'
    for tag in tags:
        cls = " active" if tag == active_sub else ""
        html += f'<span class="sub-tag{cls}" onclick="_tc(\'{primary}|{tag}\')">{tag}</span>'
    html += "</div>"
    return html

def on_tag_change(tag_value):
    parts = (tag_value or "全部|").split("|", 1)
    primary = parts[0]
    sub = parts[1] if len(parts) > 1 and parts[1] else None
    if primary == "全部":
        return _build_primary_html("全部"), "", _build_card_html(ALL_PRODUCTS)
    filtered = _filter(primary, sub) if sub else _filter(primary, None)
    sub_content = _build_sub_html(primary, sub)
    return _build_primary_html(primary), sub_content, _build_card_html(filtered)

# ─── FastAPI App ──────────────────────────────────────────────
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
import uvicorn

app = FastAPI()

# 内联 SVG 图标（取代 iconify CDN）
HEART_SVG = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 14c1.49-1.46 3-3.21 3-5.5A5.5 5.5 0 0 0 16.5 3c-1.76 0-3 .5-4.5 2-1.5-1.5-2.74-2-4.5-2A5.5 5.5 0 0 0 2 8.5c0 2.3 1.5 4.05 3 5.5l7 7Z"/></svg>'

HTML_TEMPLATE = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0,maximum-scale=1.5,viewport-fit=cover">
<meta http-equiv="Cache-Control" content="no-cache,no-store,must-revalidate">
<meta http-equiv="Pragma" content="no-cache">
<title>NAIL AI - 美甲试戴</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body,html{{margin:0;padding:0;width:100%;overflow-x:hidden;font-family:-apple-system,'PingFang SC','Microsoft YaHei',sans-serif;min-height:100vh;background:#f8f7f4;background-image:linear-gradient(180deg,rgba(196,168,130,0.03) 0%,transparent 30%),radial-gradient(ellipse at 20% 0%,rgba(196,168,130,0.06) 0%,transparent 60%),radial-gradient(ellipse at 80% 100%,rgba(196,168,130,0.04) 0%,transparent 50%)}}
@keyframes cardIn{{from{{opacity:0;transform:translateY(10px)}}to{{opacity:1;transform:translateY(0)}}}}
@keyframes shimmer{{0%{{background-position:-200% 0}}100%{{background-position:200% 0}}}}
@keyframes breath{{0%,100%{{box-shadow:0 2px 4px rgba(196,168,130,0.3)}}50%{{box-shadow:0 2px 12px rgba(196,168,130,0.55)}}}}
@keyframes ripple{{to{{transform:scale(1.5);opacity:0}}}}
.brand-title{{padding:12px 16px 0;font-size:13px;font-weight:400;letter-spacing:4px;color:#c4a882;text-align:center;text-transform:uppercase}}
.tag-row{{display:flex;overflow-x:auto;gap:0;padding:0;margin:0;scrollbar-width:none;-ms-overflow-style:none}}
.tag-row::-webkit-scrollbar{{display:none}}
.primary-tag{{flex-shrink:0;padding:6px 10px 0;font-size:13px;color:#888;cursor:pointer;white-space:nowrap;text-align:center;-webkit-tap-highlight-color:transparent;transition:color 0.25s,border-bottom-color 0.25s;border-bottom:2px solid transparent;user-select:none}}
.primary-tag:active{{opacity:0.5}}
.primary-tag.active{{color:#1a1a1a;font-weight:600;border-bottom-color:#c4a882;box-shadow:0 4px 8px -2px rgba(196,168,130,0.2)}}
.sub-row{{display:flex;overflow-x:auto;gap:4px;padding:0 10px 12px;border-bottom:1px solid #eee;scrollbar-width:none;-ms-overflow-style:none;margin-top:3px}}
.sub-row::-webkit-scrollbar{{display:none}}
.sub-tag{{flex-shrink:0;padding:3px 12px;border-radius:14px;background:#f5f3f0;border:1px solid #eee;font-size:11px;color:#888;cursor:pointer;white-space:nowrap;line-height:1.4;-webkit-tap-highlight-color:transparent;transition:all 0.2s;user-select:none}}
.sub-tag:active{{opacity:0.5;transform:scale(0.95)}}
.sub-tag.active{{background:#faf6f0;border-color:#c4a882;color:#c4a882;font-weight:500}}
.card-wrap{{display:grid;grid-template-columns:repeat(2,1fr);gap:16px;padding:0 16px 80px}}
.product-card{{border-radius:12px;overflow:hidden;background:#fff;box-shadow:0 1px 3px rgba(0,0,0,0.04),0 8px 32px rgba(196,168,130,0.08);display:flex;flex-direction:column;animation:cardIn 0.35s ease-out both;border:1px solid rgba(255,255,255,0.5);transition:transform 0.25s cubic-bezier(.22,1,.36,1),box-shadow 0.25s ease}}
.product-card:hover{{transform:translateY(-3px);box-shadow:0 4px 12px rgba(0,0,0,0.06),0 12px 48px rgba(196,168,130,0.14)}}
.product-card-img-box{{position:relative;width:100%;aspect-ratio:1/1;overflow:hidden;background:#f0eeeb}}
.product-card-img-box img{{width:100%;height:100%;object-fit:cover;display:block;animation:imgFade 0.45s ease both}}
@keyframes imgFade{{from{{opacity:0;transform:scale(0.97)}}to{{opacity:1;transform:scale(1)}}}}
.product-card-info{{padding:8px 12px;background:#fff;display:flex;flex-direction:column;justify-content:space-between;min-height:64px;border-top:1px solid rgba(196,168,130,0.3)}}
.product-card-name{{font-size:13px;color:#2a2a2a;font-weight:400;letter-spacing:0.02em;line-height:1.35;min-height:2.7em;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;word-break:break-word}}
.product-card-info-row{{display:flex;align-items:center;justify-content:space-between;gap:6px;flex-shrink:0}}
.product-card-scene{{font-size:10px;color:#999;flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.tryon-btn{{background:rgba(196,168,130,0.07);-webkit-backdrop-filter:blur(10px);backdrop-filter:blur(10px);border:0.5px solid rgba(196,168,130,0.18);color:#c4a882;padding:5px 14px;border-radius:18px;font-size:11px;font-weight:600;cursor:pointer;flex-shrink:0;white-space:nowrap;box-shadow:0 1px 4px rgba(196,168,130,0.06),inset 0 0.5px 0 rgba(255,255,255,0.3);-webkit-tap-highlight-color:transparent;transition:transform 0.15s,box-shadow 0.15s;animation:breath 2s ease-in-out infinite;position:relative;overflow:hidden}}
.tryon-btn:active{{transform:scale(0.92)!important;box-shadow:0 1px 2px rgba(196,168,130,0.03)!important;animation:none!important}}
.tryon-btn::before{{content:'';position:absolute;top:2px;left:6px;right:6px;height:8px;border-radius:50%;background:rgba(255,255,255,0.3);filter:blur(2px);pointer-events:none}}
.home-bot{{position:fixed;bottom:0;left:0;right:0;z-index:99999;display:flex;align-items:center;justify-content:center;padding:4px 16px 20px;background:rgba(255,255,255,0.75);-webkit-backdrop-filter:blur(20px);backdrop-filter:blur(20px);border-top:0.5px solid rgba(196,168,130,0.1)}}
.hbn{{cursor:pointer;-webkit-tap-highlight-color:transparent;display:flex;align-items:center;justify-content:center;gap:5px;transition:opacity 0.15s,transform 0.15s;user-select:none;background:rgba(255,255,255,0.85);-webkit-backdrop-filter:blur(16px);backdrop-filter:blur(16px);border:1px solid rgba(196,168,130,0.25);border-radius:20px;padding:5px 16px;color:#c4a882;box-shadow:0 2px 8px rgba(196,168,130,0.12)}}
.hbn:active{{opacity:0.6;transform:scale(0.95)}}
.hbn-txt{{font-size:11px;font-weight:500;line-height:1.2}}
.loading-bar{{position:fixed;top:0;left:0;z-index:999999;width:100%;height:2px;background:linear-gradient(90deg,#c4a882 30%,#e8d5c0 50%,#c4a882 70%);background-size:200% 100%;animation:loadingMove 1.2s ease infinite;display:none}}
@keyframes loadingMove{{0%{{transform:translateX(-100%)}}100%{{transform:translateX(100%)}}}}
</style>
</head>
<body>
<div class="loading-bar" id="loadingBar"></div>
<div id="primaryWrap"></div>
<div id="subWrap"></div>
<div id="productWrap"></div>
<div class="home-bot">
  <div class="hbn" id="goFavBtn">{HEART_SVG}<span class="hbn-txt">收藏与试戴记录</span></div>
</div>
<script>
function _deviceId() {{
    var d = localStorage.getItem('nail_did');
    if (!d) {{ d = 'd' + Date.now().toString(36) + Math.random().toString(36).slice(2,6); localStorage.setItem('nail_did', d); }}
    return d;
}}
function _uid() {{
    var u = localStorage.getItem('nail_uid');
    if (!u) {{ u = 'u' + Date.now().toString(36) + Math.random().toString(36).slice(2,8); localStorage.setItem('nail_uid', u); }}
    return u;
}}
window.tryImg = function(img) {{
    var num = img.dataset.num;
    if (!num) return;
    var exts = ['webp','jpg','jpeg','png'];
    var idx = 0;
    function next() {{
      if (idx >= exts.length) {{
        img.style.display = 'none';
        return;
      }}
      img.src = '/raw_images/img_' + num + '.' + exts[idx];
      idx++;
    }}
    img.onerror = next;
    next();
}};
function _tryon(id) {{
    var loc = window.location;
    window.location.href = loc.protocol + '//' + loc.hostname + ':{TRYNON_PORT}/?from=' + id + '&uid=' + _uid() + '&device=' + _deviceId() + '&_t=' + Date.now();
}}
function _goFav() {{
    var loc = window.location;
    window.location.href = loc.protocol + '//' + loc.hostname + ':{FAV_PORT}/?uid=' + _uid() + '&device=' + _deviceId() + '&tab=fav';
}}
document.getElementById('goFavBtn').onclick = _goFav;

function _loadTag(tagVal) {{
    var bar = document.getElementById('loadingBar');
    bar.style.display = 'block';
    var xhr = new XMLHttpRequest();
    xhr.open('GET', '/api/filter?tag=' + encodeURIComponent(tagVal), true);
    xhr.onload = function() {{
        bar.style.display = 'none';
        if (xhr.status === 200) {{
            var d = JSON.parse(xhr.responseText);
            document.getElementById('primaryWrap').innerHTML = d.primary;
            document.getElementById('subWrap').innerHTML = d.sub;
            document.getElementById('productWrap').innerHTML = d.products;
            var parts = (tagVal || '全部|').split('|');
            if (parts[1]) {{
                var sr = document.querySelector('.sub-row');
                if (sr) {{
                    var at = sr.querySelector('.sub-tag.active');
                    if (at) sr.scrollLeft = Math.max(0, at.offsetLeft - sr.offsetLeft - 10);
                }}
            }}
        }}
    }};
    xhr.onerror = function() {{ bar.style.display = 'none'; }};
    xhr.send();
}}

window._tc = function(t) {{
    _loadTag(t);
    var parts = t.split('|');
    if (parts[1]) {{
        var sr0 = document.querySelector('.sub-row');
        var oldContent = sr0 ? sr0.innerHTML : '';
        var tries = 0;
        var iv = setInterval(function() {{
            var sr = document.querySelector('.sub-row');
            if (sr && sr.innerHTML !== oldContent) {{
                var at = sr.querySelector('.sub-tag.active');
                if (at) {{ sr.scrollLeft = Math.max(0, at.offsetLeft - sr.offsetLeft - 10); clearInterval(iv); }}
            }}
            tries++;
            if (tries > 40) clearInterval(iv);
        }}, 80);
    }}
}};

// 初始加载
_loadTag('全部|');
</script>
</body>
</html>"""

@app.get("/", response_class=HTMLResponse)
async def index():
    return HTMLResponse(
        content=HTML_TEMPLATE,
        headers={"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache", "Expires": "0"}
    )

@app.get("/api/filter")
async def api_filter(tag: str = "全部|"):
    primary_html, sub_html, product_html = on_tag_change(tag)
    return JSONResponse({
        "primary": primary_html,
        "sub": sub_html,
        "products": product_html,
    })

# 直接提供 raw_images 目录的静态文件
@app.get("/raw_images/{filename}")
async def raw_image(filename: str):
    from fastapi.responses import FileResponse
    path = os.path.join(RAW_IMAGES_DIR, filename)
    if os.path.exists(path):
        return FileResponse(path)
    return JSONResponse({"error": "not found"}, status_code=404)

if __name__ == "__main__":
    from core.startup_check import check_all, print_report
    _sr = check_all()
    print_report(_sr, "nail_home_server")

    print(f"[nail_home_server] 启动首页服务 http://0.0.0.0:{PORT}/")
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")