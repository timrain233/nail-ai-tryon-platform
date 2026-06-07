"""
nail_tryon_server.py - FastAPI 试戴页面
保留 gradio 版完全一样的前端 HTML/CSS，只换后端 + 修复致命bug
"""
import sys, os, json

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from nail_database import dm
from nail_database.log_manager import log_behavior, update_heat_report, _read_all, BH_LOG

dm.initialize()

ALL_PRODUCTS = dm.product.read_all()
PRODUCT_CACHE = {int(p["item_id"]): p for p in ALL_PRODUCTS}
ROOT_RAW_DIR = os.path.join(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")), "assets", "raw_images")
ROOT_CUT3_DIR = os.path.join(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")), "assets", "nail_cut3")
PORT = 7885


def _get_temp_user(device_id=None):
    if not device_id:
        device_id = "TRYON_PAGE"
    return dm.get_or_create_user(device_id)


# 构建商品数据(保持和gradio版完全一致的结构)
prod_data = {}
for iid, p in PRODUCT_CACHE.items():
    scene = p.get("scene_label", "").replace(",", " · ")
    skin = p.get("skin_label", "").replace(",", " · ")
    hand = p.get("hand_label", "").replace(",", " · ")
    prod_data[str(iid)] = [scene, skin, hand]

_PROD_DATA_JSON = json.dumps(prod_data, ensure_ascii=False)

# ── 前端代码完全保留 Gradio 版 ──
_PAGE_HTML = """<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,'PingFang SC','Microsoft YaHei','Noto Sans SC',sans-serif;background:#f8f7f4;min-height:100vh;overflow-x:hidden}
@keyframes pulse{0%,100%{box-shadow:0 0 20px rgba(212,184,138,0.5),0 0 50px rgba(212,184,138,0.25),0 0 100px rgba(212,184,138,0.12),0 0 160px rgba(212,184,138,0.06);transform:translate(-50%,-50%) scale(1)}50%{box-shadow:0 0 25px rgba(212,184,138,0.6),0 0 60px rgba(212,184,138,0.35),0 0 120px rgba(212,184,138,0.18),0 0 200px rgba(212,184,138,0.08);transform:translate(-50%,-50%) scale(1.02)}}
@keyframes fadeUp{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:translateY(0)}}
@keyframes heartPop{0%{transform:scale(1)}25%{transform:scale(1.35)}50%{transform:scale(0.9)}75%{transform:scale(1.05)}100%{transform:scale(1)}}
@keyframes heartShake{0%,100%{transform:translateX(0)}15%{transform:translateX(-4px) rotate(-3deg)}30%{transform:translateX(4px) rotate(3deg)}45%{transform:translateX(-3px) rotate(-2deg)}60%{transform:translateX(3px) rotate(2deg)}75%{transform:translateX(-1px)}90%{transform:translateX(1px)}}
@keyframes favShake{0%,100%{transform:translateY(0)}20%{transform:translateY(-4px)}40%{transform:translateY(4px)}60%{transform:translateY(-3px)}80%{transform:translateY(3px)}}
@keyframes particleFly{0%{opacity:1;transform:translate(0,0) scale(1)}100%{opacity:0;transform:translate(var(--px),var(--py)) scale(0)}}
.prt{position:fixed;width:5px;height:5px;border-radius:50%;background:#e74c3c;pointer-events:none;z-index:99999999;animation:particleFly 0.6s ease-out forwards}
.bt2.liked .fav-icon{animation:favShake 0.3s ease-out !important}
.pg{min-height:100vh;background:#f8f7f4;padding-bottom:80px;background-image:linear-gradient(180deg,rgba(196,168,130,0.03) 0%,transparent 30%),radial-gradient(ellipse at 20% 0%,rgba(196,168,130,0.06) 0%,transparent 60%),radial-gradient(ellipse at 80% 100%,rgba(196,168,130,0.04) 0%,transparent 50%)}
.iv{display:flex;align-items:center;justify-content:center;padding:12px}
.fm{position:relative;width:100%;max-width:400px;border-radius:16px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,0.04),0 8px 32px rgba(196,168,130,0.08);background:#f0eeeb;min-height:300px}
.fm::before{content:'';position:absolute;inset:0;box-shadow:inset 0 -60px 40px -20px rgba(0,0,0,0.06);pointer-events:none;z-index:2}
.fm img{width:100%;display:block}
.fb{position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);width:100px;height:100px;border-radius:50%;background:rgba(255,255,255,0.06);color:#fff;border:none;font-size:14px;font-weight:700;cursor:pointer;z-index:10;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:2px;-webkit-tap-highlight-color:transparent;transition:transform 0.15s;-webkit-backdrop-filter:blur(12px);backdrop-filter:blur(12px);box-shadow:0 0 20px rgba(212,184,138,0.5),0 0 50px rgba(212,184,138,0.25),0 0 100px rgba(212,184,138,0.12),0 0 160px rgba(212,184,138,0.06),inset 0 0 0 0.5px rgba(255,255,255,0.15),inset 0 1px 0 rgba(255,255,255,0.3),inset 0 -1px 0 rgba(0,0,0,0.03);animation:pulse 2s ease-in-out infinite;text-shadow:0 1px 8px rgba(0,0,0,0.4)}.fb::before{content:'';position:absolute;top:4px;left:12px;width:34px;height:18px;border-radius:50%;background:radial-gradient(ellipse, rgba(255,255,255,0.5) 0%, transparent 70%);transform:rotate(-25deg);pointer-events:none;mix-blend-mode:overlay}.fb::after{content:'';position:absolute;bottom:14px;right:8px;width:20px;height:10px;border-radius:50%;background:radial-gradient(ellipse, rgba(255,255,255,0.2) 0%, transparent 70%);transform:rotate(20deg);pointer-events:none;mix-blend-mode:overlay}
.fb f-icon,.fb .fb-txt{opacity:1;position:relative;z-index:1}
.fb:active{transform:translate(-50%,-50%) scale(0.88)!important}
.fb f-icon{display:block;line-height:1}
.fbh{display:none!important}
.pi-row{font-size:13px;color:#555;padding:3px 0;line-height:1.5}
.pi-lab{color:#c4a882;font-weight:500;margin-right:4px}
.upp{display:none;padding:0 16px 12px;text-align:center}
.upb{width:100%;padding:14px;background:rgba(196,168,130,0.08);-webkit-backdrop-filter:blur(12px);backdrop-filter:blur(12px);border:0.5px solid rgba(196,168,130,0.25);border-radius:12px;color:#c4a882;font-size:14px;cursor:pointer;-webkit-tap-highlight-color:transparent;transition:all 0.2s;box-shadow:0 1px 4px rgba(196,168,130,0.08),inset 0 0.5px 0 rgba(255,255,255,0.3)}
.upb:active{background:rgba(196,168,130,0.15);transform:scale(0.97)}
.bot{position:fixed;bottom:0;left:0;right:0;background:rgba(255,255,255,0.82);-webkit-backdrop-filter:blur(16px);backdrop-filter:blur(16px);border-top:1px solid rgba(0,0,0,0.04);box-shadow:0 -4px 20px rgba(0,0,0,0.03);padding:6px 16px 20px;z-index:9999}
.brw{display:flex;align-items:center;justify-content:center;gap:10px}
.bn{flex:1;max-width:100px;min-height:48px;border-radius:12px;cursor:pointer;border:none;-webkit-tap-highlight-color:transparent;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:2px;padding:4px 0;transition:opacity 0.15s,transform 0.15s;user-select:none}
.bn:active{opacity:0.55;transform:scale(0.94)}
.bn-txt{font-size:10px;font-weight:500;line-height:1.2}
.bt1{background:rgba(255,255,255,0.5);-webkit-backdrop-filter:blur(8px);backdrop-filter:blur(8px);border:0.5px solid rgba(196,168,130,0.15);color:#888}
.bt2{background:rgba(255,255,255,0.5);-webkit-backdrop-filter:blur(8px);backdrop-filter:blur(8px);border:0.5px solid rgba(196,168,130,0.15);color:#888;position:relative}
.bt3{background:rgba(196,168,130,0.12);-webkit-backdrop-filter:blur(12px);backdrop-filter:blur(12px);border:0.5px solid rgba(196,168,130,0.25);color:#c4a882;box-shadow:0 1px 4px rgba(196,168,130,0.08),inset 0 0.5px 0 rgba(255,255,255,0.3)}
.bt4{background:rgba(196,168,130,0.06);-webkit-backdrop-filter:blur(8px);backdrop-filter:blur(8px);border:0.5px solid rgba(196,168,130,0.18);color:#c4a882}
.bt2.liked{background:rgba(231,76,60,0.06);border-color:rgba(231,76,60,0.2);color:#e74c3c!important}
.pi-info{padding:8px 16px 8px;margin:0 16px;background:rgba(255,255,255,0.55);-webkit-backdrop-filter:blur(8px);backdrop-filter:blur(8px);border-radius:12px;border:0.5px solid rgba(255,255,255,0.7);animation:fadeUp 0.3s ease-out}
.toast{position:fixed;bottom:90px;left:50%;transform:translateX(-50%);background:rgba(0,0,0,0.7);-webkit-backdrop-filter:blur(8px);backdrop-filter:blur(8px);color:#fff;padding:6px 18px;border-radius:20px;font-size:12px;z-index:999999;animation:toastIn 0.25s ease,toastOut 0.3s 1.5s ease forwards;pointer-events:none;border:0.5px solid rgba(255,255,255,0.1)}
@keyframes toastIn{from{opacity:0;transform:translateX(-50%) translateY(8px)}}
@keyframes toastOut{to{opacity:0;transform:translateX(-50%) translateY(-8px)}}
.ft{font-size:12px;color:#c4a882;text-align:center;padding:3px 0 0;min-height:18px}
.fav-icon{display:inline-flex;align-items:center;justify-content:center}
.fav-icon-empty,.fav-icon-filled{display:inline-flex}
.fav-icon-filled{display:none}
.bt2.liked .fav-icon-empty{display:none}
.bt2.liked .fav-icon-filled{display:inline-flex}
</style>
<div id="_err" style="display:none;color:#e74c3c;font-size:11px;padding:4px 12px;text-align:center;background:rgba(231,76,60,0.06)"></div>
<div class="pg">
  <div class="iv">
    <div class="fm">
      <img id="pi" src="" alt="" />
      <div class="fb" id="tb"><f-icon><svg width="22" height="30" viewBox="0 0 24 36" fill="none" xmlns="http://www.w3.org/2000/svg"><defs><linearGradient id="ngr" x1="12" y1="0" x2="12" y2="36" gradientUnits="userSpaceOnUse"><stop offset="0" stop-color="#d4b88a"/><stop offset="0.12" stop-color="#c4a882"/><stop offset="0.3" stop-color="#dcc298"/><stop offset="0.5" stop-color="#c4a882"/><stop offset="0.7" stop-color="#dcc298"/><stop offset="0.85" stop-color="#b89870"/><stop offset="1" stop-color="#a88860"/></linearGradient><linearGradient id="gloss" x1="5" y1="4" x2="5" y2="32" gradientUnits="userSpaceOnUse"><stop offset="0" stop-color="white" stop-opacity="0.55"/><stop offset="0.35" stop-color="white" stop-opacity="0.2"/><stop offset="1" stop-color="white" stop-opacity="0"/></linearGradient><linearGradient id="gloss2" x1="17" y1="6" x2="17" y2="30" gradientUnits="userSpaceOnUse"><stop offset="0" stop-color="white" stop-opacity="0"/><stop offset="0.5" stop-color="white" stop-opacity="0.08"/><stop offset="1" stop-color="white" stop-opacity="0"/></linearGradient></defs><path d="M12 2c-4 0-7 1.5-8 4.5C2.5 11 3 16 4 21s2 9.5 3 11c0.5 1 1.5 2 2 2.5 1 0.5 2 0.8 3 0.8s2-0.3 3-0.8c0.5-0.5 1.5-1.5 2-2.5 1-1.5 2-6 3-11s1.5-10 0-14.5C19 3.5 16 2 12 2z" fill="url(#ngr)"/><path d="M12 3.5c-3.2 0-5.5 1.2-6.5 3.8C4.2 10.5 4.8 15 5.8 20s2 8.5 2.8 10c0.5 0.8 1.2 1.5 1.7 1.8 0.7 0.4 1.5 0.6 2.5 0.6 1 0 1.8-0.2 2.5-0.6 0.5-0.3 1.2-1 1.7-1.8 0.8-1.5 1.8-5 2.8-10s1.6-9.5 0.6-12.7C17.5 4.7 15.2 3.5 12 3.5z" fill="url(#gloss)"/><path d="M12 3.5c-3.2 0-5.5 1.2-6.5 3.8C4.2 10.5 4.8 15 5.8 20s2 8.5 2.8 10c0.5 0.8 1.2 1.5 1.7 1.8 0.7 0.4 1.5 0.6 2.5 0.6 1 0 1.8-0.2 2.5-0.6 0.5-0.3 1.2-1 1.7-1.8 0.8-1.5 1.8-5 2.8-10s1.6-9.5 0.6-12.7C17.5 4.7 15.2 3.5 12 3.5z" fill="url(#gloss2)"/><path d="M7 18c1.5-1 3.5-1.5 6-1.5s4.5 0.5 6 1.5" stroke="url(#ngr)" stroke-width="0.6" opacity="0.25" stroke-linecap="round"/><path d="M7.5 22c1.5-0.8 3.5-1.2 5.5-1.2s4 0.4 5.5 1.2" stroke="url(#ngr)" stroke-width="0.6" opacity="0.2" stroke-linecap="round"/><path d="M10 4.5c-0.8 0.6-1.3 1.5-1.6 2.5" stroke="white" stroke-width="0.5" opacity="0.3" stroke-linecap="round"/><path d="M14 4.5c0.8 0.6 1.3 1.5 1.6 2.5" stroke="white" stroke-width="0.5" opacity="0.25" stroke-linecap="round"/><path d="M9 6C8 7.5 7.8 9.5 7.8 11.5" stroke="white" stroke-width="0.6" opacity="0.12" stroke-linecap="round"/><path d="M15 6C16 7.5 16.2 9.5 16.2 11.5" stroke="white" stroke-width="0.6" opacity="0.08" stroke-linecap="round"/></svg></f-icon><span class="fb-txt">试戴</span></div>
    </div>
  </div>
  <div class="pi-info" id="pi-info">
    <div class="pi-row" id="pi-row1"><span class="pi-lab">场景</span><span id="pi-v1"></span></div>
    <div class="pi-row" id="pi-row2"><span class="pi-lab">肤色</span><span id="pi-v2"></span></div>
    <div class="pi-row" id="pi-row3"><span class="pi-lab">手型</span><span id="pi-v3"></span></div>
  </div>
  <div class="upp" id="us">
    <div class="upb" id="upb"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:middle;margin-right:4px"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="M21 15l-5-5L5 21"/></svg> 选择手部照片</div>
    <div style="font-size:12px;color:#999;padding:4px 0 0;min-height:18px"></div>
  </div>
</div>
<div class="bot">
  <div class="brw">
    <div class="bn bt1" id="goHome"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 12H5"/><path d="M12 19l-7-7 7-7"/></svg><span class="bn-txt">退出</span></div>
    <div class="bn bt2" id="doFav">
      <span class="fav-icon"><span class="fav-icon-empty"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 14c1.49-1.46 3-3.21 3-5.5A5.5 5.5 0 0 0 16.5 3c-1.76 0-3 .5-4.5 2-1.5-1.5-2.74-2-4.5-2A5.5 5.5 0 0 0 2 8.5c0 2.3 1.5 4.05 3 5.5l7 7Z"/></svg></span><span class="fav-icon-filled"><svg width="20" height="20" viewBox="0 0 24 24" fill="#e74c3c" stroke="#e74c3c" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 14c1.49-1.46 3-3.21 3-5.5A5.5 5.5 0 0 0 16.5 3c-1.76 0-3 .5-4.5 2-1.5-1.5-2.74-2-4.5-2A5.5 5.5 0 0 0 2 8.5c0 2.3 1.5 4.05 3 5.5l7 7Z"/></svg></span></span>
      <span class="bn-txt">收藏</span>
    </div>
    <div class="bn bt3" id="doShare"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/><circle cx="18" cy="19" r="3"/><path d="M8.59 13.51l6.83 3.98"/><path d="M15.41 6.51l-6.82 3.98"/></svg><span class="bn-txt">分享</span></div>
    <div class="bn bt4" id="goFav"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 14c1.49-1.46 3-3.21 3-5.5A5.5 5.5 0 0 0 16.5 3c-1.76 0-3 .5-4.5 2-1.5-1.5-2.74-2-4.5-2A5.5 5.5 0 0 0 2 8.5c0 2.3 1.5 4.05 3 5.5l7 7Z"/></svg><span class="bn-txt">收藏记录</span></div>
  </div>
  <div class="ft" id="ft"></div>
</div>"""

# ── JS 代码：全新版，基于 localStorage uid + sessionStorage pid + form表单API ──
_JS_INIT = """var _PROD_DATA = """ + _PROD_DATA_JSON + """;

window.onerror = function(m, u, l) {
    var e = document.getElementById('_err');
    if (e) { e.style.display = ''; e.textContent = 'JS\u9519\u8BEF: ' + m + ' (' + l + ')'; }
};

(function() {
try {
    // 1. 设备唯一 user_id（localStorage 持久化，退出重进不丢失）
    var _uid = localStorage.getItem('nail_uid');
    if (!_uid) {
        _uid = 'u' + Date.now().toString(36) + Math.random().toString(36).slice(2, 8);
        localStorage.setItem('nail_uid', _uid);
    }
    window._tryonUid = _uid;

    // 2. 商品 ID：从 URL 读 + 写入 sessionStorage（退出再进从存储恢复）
    var _urlPid = new URLSearchParams(window.location.search).get('from') || '';
    if (_urlPid) sessionStorage.setItem('tryon_pid', _urlPid);
    var pid = sessionStorage.getItem('tryon_pid') || _urlPid || '';
    var num = pid ? ('000' + pid).slice(-3) : '';
    var deviceId = new URLSearchParams(window.location.search).get('device') || '';
    window._tryonDeviceId = deviceId;

    // 3. 渲染主图
    var img = document.getElementById('pi');
    if (img && num) {
        var exts = ['webp', 'jpg', 'jpeg', 'png'];
        var idx = 0;
        function next() {
            if (idx >= exts.length) { img.style.display = 'none'; return; }
            img.src = '/raw_images/img_' + num + '.' + exts[idx];
            idx++;
        }
        img.onerror = next;
        next();
    }

    // 4. 初始化收藏状态（调本服务 API）
    function _setFav(liked) {
        var favBtn = document.getElementById('doFav');
        if (!favBtn) return;
        if (liked) { favBtn.classList.add('liked'); favBtn._liked = true; }
        else { favBtn.classList.remove('liked'); favBtn._liked = false; }
    }

    if (pid && _uid) {
        var ckXhr = new XMLHttpRequest();
        ckXhr.open('GET', '/api/check_fav?uid=' + encodeURIComponent(_uid) + '&pid=' + encodeURIComponent(pid), true);
        ckXhr.timeout = 5000;
        ckXhr.onload = function() {
            try { var r = JSON.parse(ckXhr.responseText); if (r && r.data === '1') _setFav(true); } catch(e) {}
        };
        ckXhr.send();
    }

    // 5. 渲染商品信息
    if (pid && _PROD_DATA[pid]) {
        var p = _PROD_DATA[pid];
        var el1 = document.getElementById('pi-v1');
        var el2 = document.getElementById('pi-v2');
        var el3 = document.getElementById('pi-v3');
        if (p[0] && el1) el1.textContent = p[0];
        if (p[1] && el2) el2.textContent = p[1];
        if (p[2] && el3) el3.textContent = p[2];
    }

    // 6. 文件上传 + 试戴渲染
    var fi = document.createElement('input');
    fi.type = 'file';
    fi.accept = 'image/*';
    fi.style.position = 'fixed';
    fi.style.top = '-9999px';
    fi.style.left = '-9999px';
    fi.style.opacity = '0';
    document.body.appendChild(fi);
    var _renderLoading = false;
    fi.addEventListener('change', function(e) {
        if (e.target.files && e.target.files[0]) {
            var file = e.target.files[0];
            if (img) img.src = URL.createObjectURL(file);
            document.getElementById('ft').textContent = '\u6E32\u67D3\u4E2D...';
            _renderLoading = true;
            var fd = new FormData();
            fd.append('file', file);
            fd.append('product_id', pid || '0');
            fd.append('user_id', _uid);
            fd.append('device_id', _uid);
            var xhr = new XMLHttpRequest();
            xhr.open('POST', '//' + window.location.hostname + ':7887/render', true);
            xhr.onload = function() {
                try {
                    var resp = JSON.parse(xhr.responseText);
                    if (resp.result) {
                        img.src = 'data:image/png;base64,' + resp.result;
                        var info = '';
                        if (resp.total_nails > 0) info = '\u68C0\u6D4B\u5230 ' + resp.total_nails + ' \u4E2A\u6307\u7532\uFF0C\u6210\u529F\u6E32\u67D3 ' + resp.success_nails + ' \u4E2A';
                        document.getElementById('ft').textContent = info || '\u8BD5\u6234\u5B8C\u6210';
                    } else if (resp.error) {
                        document.getElementById('ft').textContent = '\u6E32\u67D3\u5931\u8D25: ' + resp.error;
                    }
                } catch(ex) { document.getElementById('ft').textContent = '\u6E32\u67D3\u51FA\u9519'; }
                _renderLoading = false;
            };
            xhr.onerror = function() { document.getElementById('ft').textContent = '\u6E32\u67D3\u670D\u52A1\u672A\u8FDE\u63A5'; _renderLoading = false; };
            xhr.send(fd);
        }
    });

    function _spawnParticles(el) {
        var rect = el.getBoundingClientRect();
        var cx = rect.left + rect.width / 2, cy = rect.top + rect.height / 2;
        var colors = ['#e74c3c','#ff6b6b','#ff4757','#ff2d55','#ff7675','#fd79a8','#e84393','#ff9ff3'];
        var sizes = [12, 14, 16, 18, 20, 22];
        var hPath = 'M19 14c1.49-1.46 3-3.21 3-5.5A5.5 5.5 0 0 0 16.5 3c-1.76 0-3 .5-4.5 2-1.5-1.5-2.74-2-4.5-2A5.5 5.5 0 0 0 2 8.5c0 2.3 1.5 4.05 3 5.5l7 7Z';
        for (var i = 0; i < 20; i++) {
            var p = document.createElement('div');
            var sz = sizes[i % sizes.length];
            var cl = colors[i % colors.length];
            var a = Math.random() * 360;
            var d = 30 + Math.random() * 90;
            var dur = 0.5 + Math.random() * 0.4;
            p.innerHTML = '<svg width="' + sz + '" height="' + sz + '" viewBox="0 0 24 24" fill="' + cl + '" stroke="' + cl + '"><path d="' + hPath + '"/></svg>';
            p.style.cssText = 'position:fixed;left:' + cx + 'px;top:' + cy + 'px;pointer-events:none;z-index:99999999;animation:particleFly ' + dur + 's ease-out forwards';
            p.style.setProperty('--px', Math.cos(a * Math.PI / 180) * d + 'px');
            p.style.setProperty('--py', Math.sin(a * Math.PI / 180) * d + 'px');
            document.body.appendChild(p);
            setTimeout(function(pt) { if (pt && pt.remove) pt.remove(); }, 1000, p);
        }
    }

    function showToast(msg) {
        var t = document.createElement('div');
        t.className = 'toast';
        t.textContent = msg;
        document.body.appendChild(t);
        setTimeout(function(){ if(t && t.remove) t.remove(); }, 2000);
    }

    // 7. 按钮事件
    document.getElementById('tb').onclick = function() {
        try { navigator.vibrate(12); } catch(e) {}
        document.getElementById('us').style.display = '';
        fi.value = ''; fi.click();
    };
    document.getElementById('upb').onclick = function() { fi.value = ''; fi.click(); };
    document.getElementById('goHome').onclick = function() {
        sessionStorage.removeItem('tryon_pid');
        window.location.href = window.location.protocol + '//' + window.location.hostname + ':7860/';
    };
    document.getElementById('doShare').onclick = function() {
        document.getElementById('ft').textContent = '\u94FE\u63A5\u5DF2\u590D\u5236';
    };
    document.getElementById('goFav').onclick = function() {
        window.location.href = window.location.protocol + '//' + window.location.hostname + ':7886/?uid=' + encodeURIComponent(_uid) + '&tab=fav';
    };

    // 8. form 表单 POST 工具
    function _formPost(url, data, onOk, onErr) {
        var x = new XMLHttpRequest();
        x.open('POST', url, true);
        x.setRequestHeader('Content-Type', 'application/x-www-form-urlencoded');
        x.timeout = 8000;
        x.onload = function() { try { onOk(JSON.parse(x.responseText)); } catch(e) { onErr(); } };
        x.ontimeout = onErr; x.onerror = onErr;
        var body = [];
        for (var k in data) { if (data.hasOwnProperty(k)) body.push(encodeURIComponent(k) + '=' + encodeURIComponent(data[k])); }
        x.send(body.join('&'));
    }

    // 9. 收藏 / 取消收藏（新 API：form 表单 POST）
    document.getElementById('doFav').onclick = function() {
        if (!pid) { document.getElementById('ft').textContent = '\u8BF7\u5148\u9009\u62E9\u6B3E\u5F0F'; return; }
        if (this._liking) {
            if (this._likingTime && Date.now() - this._likingTime > 10000) { this._liking = false; this._likingTime = 0; }
            else { return; }
        }
        try { navigator.vibrate(30); } catch(e) {}
        var me = this;
        if (!me._liked) {
            var fiEl = me.querySelector('.fav-icon');
            if (fiEl) { fiEl.style.animation = 'none'; void fiEl.offsetWidth; fiEl.style.animation = 'favShake 0.3s ease-out'; }
        }
        function _onFinish(liked) {
            if (liked) { me.classList.add('liked'); me._liked = true; document.getElementById('ft').textContent = '\u5DF2\u6536\u85CF'; showToast('\u5DF2\u6536\u85CF'); _spawnParticles(me); }
            else { me.classList.remove('liked'); me._liked = false; document.getElementById('ft').textContent = '\u5DF2\u53D6\u6D88'; showToast('\u5DF2\u53D6\u6D88\u6536\u85CF'); }
            me._liking = false; me._likingTime = 0;
        }
        if (me._liked) {
            me._liking = true; me._likingTime = Date.now();
            _formPost('/api/favorite', {user_id: _uid, product_id: pid, op: '0'},
                function() { _onFinish(false); }, function() { _onFinish(false); }
            );
        } else {
            me._liking = true; me._likingTime = Date.now();
            _spawnParticles(me);
            _formPost('/api/favorite', {user_id: _uid, product_id: pid, op: '1'},
                function() { _onFinish(true); }, function() { _onFinish(true); }
            );
        }
    };

} catch(e) {
    var ed = document.getElementById('_err');
    if (ed) { ed.style.display = ''; ed.textContent = '\u521D\u59CB\u5316\u9519\u8BEF: ' + (e.message || e); }
}
})();"""

_FULL_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0,maximum-scale=1.5,viewport-fit=cover">
<title>NAIL AI - 试戴</title>
</head>
<body>
""" + _PAGE_HTML + """
<script>
""" + _JS_INIT + """
</script>
</body>
</html>"""


# ── FastAPI 服务 ─────────────────────────────────────────

if __name__ == "__main__":
    from core.startup_check import check_all, print_report
    _sr = check_all()
    print_report(_sr, "nail_tryon_server")

    import uvicorn
    from fastapi import FastAPI, Request
    from fastapi.responses import HTMLResponse, JSONResponse
    from fastapi.staticfiles import StaticFiles
    from fastapi.middleware.cors import CORSMiddleware

    app = FastAPI(title="NAIL AI - 试戴")
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

    # 挂载 raw_images，支持 Gradio 兼容路径
    if os.path.isdir(ROOT_RAW_DIR):
        app.mount("/gradio_api/file/raw_images", StaticFiles(directory=ROOT_RAW_DIR), name="gradio_raw")
        app.mount("/raw_images", StaticFiles(directory=ROOT_RAW_DIR), name="raw_images")
    if os.path.isdir(ROOT_CUT3_DIR):
        app.mount("/raw_image", StaticFiles(directory=ROOT_CUT3_DIR), name="nail_cut3")

    @app.get("/")
    async def index():
        return HTMLResponse(
            content=_FULL_HTML,
            headers={"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache", "Expires": "0"}
        )

    # 保留旧 API（兼容已缓存的客户端）
    @app.post("/gradio_api/call/on_favorite")
    async def gradio_on_favorite(request: Request):
        body = await request.json()
        data_list = body.get("data", [])
        item_id_val = data_list[0] if len(data_list) > 0 else 0
        device_id_val = data_list[1] if len(data_list) > 1 else ""
        try:
            user = _get_temp_user(device_id_val)
            uid = user["temp_user_id"]
            iid = int(item_id_val) if item_id_val else 0
            if iid > 0:
                dm.add_behavior_log(uid, "点击收藏", item_id=iid)
                p = PRODUCT_CACHE.get(iid, {})
                name = p.get("item_name", "")
                dm.user.append_user_tag(uid, "喜欢_" + (name or "未知"))
                log_behavior(str(uid), str(iid), "favorite", True, 0, device_id_val or "")
                update_heat_report(str(iid))
        except Exception:
            pass
        return {"data": "已收藏"}

    @app.post("/gradio_api/call/on_unfavorite")
    async def gradio_on_unfavorite(request: Request):
        body = await request.json()
        data_list = body.get("data", [])
        item_id_val = data_list[0] if len(data_list) > 0 else 0
        device_id_val = data_list[1] if len(data_list) > 1 else ""
        try:
            user = _get_temp_user(device_id_val)
            uid = user["temp_user_id"]
            iid = int(item_id_val) if item_id_val else 0
            if iid > 0:
                dm.add_behavior_log(uid, "取消收藏", item_id=iid)
                log_behavior(str(uid), str(iid), "unfavorite", True, 0, device_id_val or "")
        except Exception:
            pass
        return {"data": "已取消"}

    # ── 新 API：收藏/取消收藏（form 表单） ──
    @app.post("/api/favorite")
    async def api_favorite(request: Request):
        body = await request.form()
        user_id = body.get("user_id", "")
        product_id = body.get("product_id", "")
        op = body.get("op", "1")
        action = "favorite" if op == "1" else "unfavorite"
        action_cn = "收藏" if op == "1" else "取消收藏"
        try:
            iid = int(product_id) if product_id else 0
            if iid > 0 and user_id:
                log_behavior(str(user_id), str(iid), action, True, 0, user_id)
                update_heat_report(str(iid), "")
                dm.add_behavior_log(str(user_id), action_cn, item_id=iid)
        except Exception as e:
            print(f"[api_favorite] err: {e}")
        return {"code": 0, "msg": action_cn + "成功"}

    # ── 新 API：检查是否已收藏 ──
    @app.get("/api/check_fav")
    async def api_check_fav(uid: str = "", pid: str = ""):
        try:
            rows = _read_all(BH_LOG)
            for r in reversed(rows):
                if r.get("user_id") == uid and r.get("product_id") == str(int(pid) if pid else 0):
                    action = r.get("action", "")
                    if action == "favorite":
                        return {"code": 0, "data": "1"}
                    elif action == "unfavorite":
                        return {"code": 0, "data": "0"}
            return {"code": 0, "data": "0"}
        except Exception as e:
            print(f"[api_check_fav] err: {e}")
            return {"code": 0, "data": "0"}

    print(f"[nail_tryon_server] 启动 http://0.0.0.0:{PORT}/")
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")