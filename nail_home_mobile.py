"""
nail_home_mobile.py - 美甲AI试戴首页
使用Gradio 5.x /gradio_api/file/ 文件URL, 无需base64编码, 加载瞬间完成
"""

import gradio as gr
import os
import sys
import csv
from packaging.version import Version

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from nail_database import dm

dm.initialize()

ALL_PRODUCTS = dm.product.read_all()
RAW_IMAGES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "raw_images")

PRIMARY_TAGS = ["全部", "场合", "款式", "颜色", "长度", "甲型"]

TAG_CSV_MAP = {
    "场合": "scene_label",
    "款式": "style_label",
    "颜色": "color_label",
    "长度": "length_label",
    "甲型": "nail_shape_label",
}

TRYNON_PORT = 7885
FAV_PORT = 7886


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
        path = os.path.join("raw_images", fname)
        if os.path.exists(os.path.join(RAW_IMAGES_DIR, fname)):
            return f"/gradio_api/file/{path}"
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
        cards += f"""
<div class="product-card" style="animation-delay:{delay}s">
  <div class="product-card-img-box">
    <img src="{img}" alt="{name}" loading="lazy" />
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


def _build_primary_html(active):
    html = '<div class="brand-title">NAIL</div>'
    html += '<div class="tag-row">'
    for tag in PRIMARY_TAGS:
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
        return _build_primary_html("全部"), gr.update(value="", visible=False), _build_card_html(ALL_PRODUCTS)

    filtered = _filter(primary, sub) if sub else _filter(primary, None)
    sub_content = _build_sub_html(primary, sub)
    return _build_primary_html(primary), gr.update(value=sub_content, visible=True), _build_card_html(filtered)


_BOTTOM_JS = """() => {
    var bar = document.querySelector('.home-bot');
    if (bar) return;
    var s = document.createElement('style');
    s.textContent = '.home-bot{position:fixed;bottom:0;left:0;right:0;z-index:99999;display:flex;align-items:center;justify-content:center;padding:1px 16px 20px}.hbn{cursor:pointer;-webkit-tap-highlight-color:transparent;display:flex;align-items:center;justify-content:center;gap:5px;transition:opacity 0.15s,transform 0.15s;user-select:none;background:rgba(255,255,255,0.85);-webkit-backdrop-filter:blur(16px);backdrop-filter:blur(16px);border:1px solid rgba(196,168,130,0.25);border-radius:20px;padding:5px 16px;color:#c4a882;box-shadow:0 2px 8px rgba(196,168,130,0.12)}.hbn:active{opacity:0.6;transform:scale(0.95)}.hbn-txt{font-size:11px;font-weight:500;line-height:1.2}';
    document.head.appendChild(s);
    var d = document.createElement('div');
    d.className = 'home-bot';
    d.innerHTML = '<div class="hbn" onclick="window._goFav()"><iconify-icon icon="lucide:heart" width="15" height="15"></iconify-icon><span class="hbn-txt">收藏与试戴记录</span></div>';
    document.body.appendChild(d);
}"""


_JS_BRIDGE = f"""
() => {{
    window._tc = function(t) {{
        var parts = t.split('|');
        var sub = parts[1] || '';
        var c = document.getElementById('tag-i');
        if (!c) return;
        var e = c.querySelector('input, textarea');
        if (!e) return;
        e.value = t;
        e.dispatchEvent(new Event('input', {{bubbles: true}}));
        e.dispatchEvent(new Event('change', {{bubbles: true}}));
        if (sub) {{
            var sr0 = document.querySelector('.sub-row');
            var oldContent = sr0 ? sr0.innerHTML : '';
            var tries = 0;
            var iv = setInterval(function() {{
                var sr = document.querySelector('.sub-row');
                if (sr && sr.innerHTML !== oldContent) {{
                    var at = sr.querySelector('.sub-tag.active');
                    if (at) {{
                        sr.scrollLeft = Math.max(0, at.offsetLeft - sr.offsetLeft - 10);
                        clearInterval(iv);
                    }}
                }}
                tries++;
                if (tries > 40) clearInterval(iv);
            }}, 80);
        }}
    }};
    window._deviceId = function() {{
        var d = localStorage.getItem('nail_did');
        if (!d) {{
            d = 'd' + Date.now().toString(36) + Math.random().toString(36).slice(2,6);
            localStorage.setItem('nail_did', d);
        }}
        return d;
    }};
    window._tryon = function(id) {{
        var port = {TRYNON_PORT};
        var loc = window.location;
        window.location.href = loc.protocol + '//' + loc.hostname + ':' + port + '/?from=' + id + '&device=' + _deviceId();
    }};
    window._goFav = function() {{
        var port = {FAV_PORT};
        var loc = window.location;
        window.location.href = loc.protocol + '//' + loc.hostname + ':' + port + '/?device=' + _deviceId() + '&tab=fav';
    }};
}}
"""

CUSTOM_CSS = """
body,html{margin:0;padding:0;width:100%;overflow-x:hidden;font-family:'Noto Sans SC',-apple-system,'PingFang SC','Microsoft YaHei',sans-serif;min-height:100vh;background:#f8f7f4;background-image:linear-gradient(180deg,rgba(196,168,130,0.03) 0%,transparent 30%),radial-gradient(ellipse at 20% 0%,rgba(196,168,130,0.06) 0%,transparent 60%),radial-gradient(ellipse at 80% 100%,rgba(196,168,130,0.04) 0%,transparent 50%)}
@keyframes cardIn{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:translateY(0)}}
@keyframes shimmer{0%{background-position:-200% 0}100%{background-position:200% 0}}
@keyframes breath{0%,100%{box-shadow:0 2px 4px rgba(196,168,130,0.3)}50%{box-shadow:0 2px 12px rgba(196,168,130,0.55)}}
@keyframes ripple{to{transform:scale(1.5);opacity:0}}
.brand-title{padding:12px 16px 0;font-size:13px;font-weight:400;letter-spacing:4px;color:#c4a882;text-align:center;text-transform:uppercase}
.gradio-container{width:100%!important;max-width:100vw!important;padding:0!important;margin:0!important;border:none!important;border-radius:0!important}
.wrap,.main,.container,.gradio-container .contain{padding:0!important;margin:0!important;gap:0!important}
.card-wrap{display:grid;grid-template-columns:repeat(2,1fr);gap:16px;padding:0 0 80px!important}
.tag-row{display:flex;overflow-x:auto;gap:0;padding:0;margin:0;scrollbar-width:none;-ms-overflow-style:none}
.tag-row::-webkit-scrollbar{display:none}
.primary-tag{flex-shrink:0;padding:6px 10px 0;font-size:13px;color:#888;cursor:pointer;white-space:nowrap;text-align:center;-webkit-tap-highlight-color:transparent;transition:color 0.25s,border-bottom-color 0.25s;border-bottom:2px solid transparent}
.primary-tag:active{opacity:0.5}
.primary-tag.active{color:#1a1a1a;font-weight:600;border-bottom-color:#c4a882;box-shadow:0 4px 8px -2px rgba(196,168,130,0.2)}
.sub-row{display:flex;overflow-x:auto;gap:4px;padding:0 10px 4px;border-bottom:1px solid #eee;scrollbar-width:none;-ms-overflow-style:none;margin-top:-1px}
.sub-row::-webkit-scrollbar{display:none}
.sub-tag{flex-shrink:0;padding:3px 12px;border-radius:14px;background:#f5f3f0;border:1px solid #eee;font-size:11px;color:#888;cursor:pointer;white-space:nowrap;line-height:1.4;-webkit-tap-highlight-color:transparent;transition:all 0.2s}
.sub-tag:active{opacity:0.5;transform:scale(0.95)}
.sub-tag.active{background:#faf6f0;border-color:#c4a882;color:#c4a882;font-weight:500}
#tag-i{position:fixed!important;top:-9999px!important;left:-9999px!important;opacity:0!important;height:1px!important;width:1px!important;overflow:hidden!important;pointer-events:none!important;z-index:-1!important}
#tag-i label{display:none!important}
.product-card{border-radius:10px;overflow:hidden;background:#fff;box-shadow:0 1px 3px rgba(0,0,0,0.04),0 8px 32px rgba(196,168,130,0.08);display:flex;flex-direction:column;animation:cardIn 0.35s ease-out both;border:1px solid rgba(255,255,255,0.5)}
.product-card-img-box{position:relative;width:100%;aspect-ratio:1/1;overflow:hidden;background:#f0eeeb}
.product-card-img-box::before{content:'';position:absolute;inset:0;background:linear-gradient(90deg,#f0eeeb 25%,#e8e4df 50%,#f0eeeb 75%);background-size:200% 100%;animation:shimmer 1.5s infinite;z-index:0}
.product-card-img-box::after{content:'';position:absolute;inset:0;box-shadow:inset 0 -40px 30px -20px rgba(0,0,0,0.04);pointer-events:none;z-index:2}
.product-card-img-box img{position:relative;z-index:1;width:100%;height:100%;object-fit:cover;display:block}
.product-card-info{padding:8px 12px;background:#fff;display:flex;flex-direction:column;justify-content:space-between;min-height:64px;border-top:1px solid rgba(196,168,130,0.3)}
.product-card-name{font-size:13px;color:#2a2a2a;font-weight:400;letter-spacing:0.02em;line-height:1.35;min-height:2.7em;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;word-break:break-word}
.product-card-info-row{display:flex;align-items:center;justify-content:space-between;gap:6px;flex-shrink:0}
.product-card-scene{font-size:10px;color:#999;flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.tryon-btn{background:#c4a882;color:#fff;border:none;padding:5px 14px;border-radius:18px;font-size:11px;font-weight:600;cursor:pointer;flex-shrink:0;white-space:nowrap;box-shadow:0 2px 4px rgba(196,168,130,0.3);-webkit-tap-highlight-color:transparent;transition:transform 0.15s,box-shadow 0.15s;animation:breath 2s ease-in-out infinite;position:relative;overflow:hidden}
.tryon-btn:active{transform:scale(0.92)!important;box-shadow:0 1px 2px rgba(196,168,130,0.2)!important;animation:none!important}
.tryon-btn::after{content:'';position:absolute;inset:0;border-radius:18px;background:rgba(255,255,255,0.15);transform:scale(0);opacity:0}
.tryon-btn:active::after{animation:ripple 0.3s ease-out}
"""


def create_interface():
    HEAD_INJECT = '<meta http-equiv="Cache-Control" content="no-cache,no-store,must-revalidate"><meta http-equiv="Pragma" content="no-cache"><meta name="viewport" content="width=device-width,initial-scale=1.0,maximum-scale=1.5,viewport-fit=cover"><script src="https://code.iconify.design/iconify-icon/2.1.0/iconify-icon.min.js"></script><script src="https://cdnjs.cloudflare.com/ajax/libs/animejs/3.2.2/anime.min.js"></script><style>html,body{margin:0!important;padding:0!important;overflow-x:hidden!important;width:100%!important;max-width:100vw!important}body{min-height:100dvh}gradio-app{display:block;width:100%!important;max-width:100vw!important;overflow-x:hidden!important}.contain,.main,.wrap,.container,.gradio-container .contain{max-width:100vw!important;width:100%!important;padding-left:0!important;padding-right:0!important;margin-left:0!important;margin-right:0!important}.gap,.form{max-width:100vw!important;width:100%!important;padding-left:0!important;padding-right:0!important}.progress-bar,.progress-text,.loading{display:none!important}</style>'

    with gr.Blocks(css=CUSTOM_CSS, title="NAIL AI - 美甲试戴", head=HEAD_INJECT) as demo:
        tag_i = gr.Textbox(value="全部|", elem_id="tag-i", label="")

        primary_html = gr.HTML(value=_build_primary_html("全部"))
        sub_html = gr.HTML(value="", visible=False)
        product_html = gr.HTML(value=_build_card_html(ALL_PRODUCTS))

        tag_i.change(
            fn=on_tag_change,
            inputs=[tag_i],
            outputs=[primary_html, sub_html, product_html],
        )

        if Version(gr.__version__) >= Version("4.0"):
            demo.load(fn=None, js=_JS_BRIDGE)
            demo.load(fn=None, js="""()=>{var s=document.createElement('style');s.textContent='gradio-app{transform:scale(1.15);transform-origin:center top}';document.head.appendChild(s);var i=setInterval(function(){var e=document.querySelector('gradio-app');if(e&&e.shadowRoot){var c=e.shadowRoot.querySelector('.contain');if(c){c.style.paddingLeft='0';c.style.paddingRight='0';c.style.maxWidth='100vw';c.style.width='100%';clearInterval(i)}}},100);setTimeout(function(){clearInterval(i)},5000)}""")
            demo.load(fn=None, js=_BOTTOM_JS)
        else:
            demo.load(_js=_JS_BRIDGE)
            demo.load(_js=_BOTTOM_JS)

    return demo


if __name__ == "__main__":
    create_interface().launch(server_name="0.0.0.0", server_port=7860,
                              share=False, allowed_paths=["raw_images"])
