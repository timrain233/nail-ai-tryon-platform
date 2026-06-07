"""
nail_tryon_page.py - 试戴页面
JS用fetch调用Gradio API, HTML渲染在main DOM中, 完全绕过Shadow DOM
"""

import gradio as gr
import os, sys, threading, json

_PROJ_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, _PROJ_ROOT)
from nail_database import dm

dm.initialize()

ROOT_RAW_DIR = os.path.join(_PROJ_ROOT, "assets", "raw_images")
ALL_PRODUCTS = dm.product.read_all()
PRODUCT_CACHE = {int(p["item_id"]): p for p in ALL_PRODUCTS}


def _get_temp_user(device_id=None):
    if not device_id:
        device_id = "TRYON_PAGE"
    cache_key = f"user_{device_id}"
    return dm.get_or_create_user(device_id)


def on_favorite(item_id_val, device_id_val=None):
    user = _get_temp_user(device_id_val)
    uid = user["temp_user_id"]
    try:
        iid = int(item_id_val)
    except (ValueError, TypeError):
        iid = 0
    if iid <= 0:
        return "请先选择款式"
    dm.add_behavior_log(uid, "点击收藏", item_id=iid)
    p = PRODUCT_CACHE.get(iid, {})
    name = p.get("item_name", "")
    dm.user.append_user_tag(uid, "喜欢_" + (name or "未知"))
    # 同时写入新日志，保证 fav_page 能找到
    try:
        from nail_database.log_manager import log_behavior, update_heat_report
        log_behavior(str(uid), str(iid), "favorite", True, 0, device_id_val or "")
        update_heat_report(str(iid))
    except Exception:
        pass
    return "已收藏"


def on_share():
    user = _get_temp_user()
    uid = user["temp_user_id"]
    dm.add_behavior_log(uid, "保存图片")
    return "链接已复制"


def on_check_favorite(item_id_val, device_id_val=None):
    if not device_id_val or not item_id_val:
        return "0"
    try:
        iid = int(item_id_val)
    except (ValueError, TypeError):
        return "0"
    if iid <= 0:
        return "0"
    user = _get_temp_user(device_id_val)
    uid = user["temp_user_id"]
    logs = dm.log.get_user_logs(uid)
    for log in reversed(logs):
        if int(log.get("item_id", 0)) == iid:
            op = log.get("operate_type", "")
            if op == "点击收藏":
                return "1"
            if op == "取消收藏":
                return "0"
    return "0"


def on_unfavorite(item_id_val, device_id_val=None):
    user = _get_temp_user(device_id_val)
    uid = user["temp_user_id"]
    try:
        iid = int(item_id_val)
    except (ValueError, TypeError):
        iid = 0
    if iid <= 0:
        return "已取消"
    dm.add_behavior_log(uid, "取消收藏", item_id=iid)
    try:
        from nail_database.log_manager import log_behavior
        log_behavior(str(uid), str(iid), "unfavorite", True, 0, device_id_val or "")
    except Exception:
        pass
    return "已取消"


CUSTOM_CSS = """
body,html{margin:0;padding:0;width:100%;background:#f8f7f4;font-family:-apple-system,'PingFang SC','Microsoft YaHei',sans-serif}
.gradio-container{width:100%!important;max-width:100vw!important;padding:0!important;margin:0!important;border:none!important;border-radius:0!important}
#sel-id,#fav-bt,#share-bt,#fb-bx,#unfav-bt,#dev-inp,#chk-bx,#chk-bt{position:fixed!important;top:-9999px!important;left:-9999px!important;opacity:0!important;height:1px!important;width:1px!important;overflow:hidden!important;z-index:-1!important}
#sel-id label,#fav-bt label,#share-bt label,#fb-bx label,#unfav-bt label,#dev-inp label,#chk-bx label,#chk-bt label{display:none!important}
"""


_PAGE_HTML = """<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,'PingFang SC','Microsoft YaHei','Noto Sans SC',sans-serif;background:#f8f7f4;min-height:100vh;overflow-x:hidden}
@keyframes pulse{0%,100%{box-shadow:0 4px 20px rgba(196,168,130,0.4);transform:translate(-50%,-50%) scale(1)}50%{box-shadow:0 4px 40px rgba(196,168,130,0.6);transform:translate(-50%,-50%) scale(1.04)}}
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
.fm::after{content:'';position:absolute;bottom:0;left:0;right:0;height:50%;background:linear-gradient(to top,rgba(196,168,130,0.25),transparent);pointer-events:none;z-index:3}
.fm img{width:100%;display:block}
.fb{position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);width:90px;height:90px;border-radius:50%;background:rgba(196,168,130,0.08);color:#fff;border:none;font-size:13px;font-weight:600;cursor:pointer;z-index:999999;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:1px;-webkit-tap-highlight-color:transparent;transition:transform 0.15s,box-shadow 0.15s;backdrop-filter:blur(4px);-webkit-backdrop-filter:blur(4px);box-shadow:0 4px 24px rgba(196,168,130,0.25),inset 0 1px 0 rgba(255,255,255,0.2),inset 0 -1px 0 rgba(0,0,0,0.04);animation:pulse 2s ease-in-out infinite}
.fb f-icon,.fb .fb-txt{opacity:1;position:relative;z-index:1}
.fb:active{transform:translate(-50%,-50%) scale(0.88)!important}
.fb f-icon{display:block;line-height:1}
.fbh{display:none!important}
.pi-info{padding:4px 16px 8px;animation:fadeUp 0.3s ease-out}
.pi-row{font-size:13px;color:#555;padding:3px 0;line-height:1.5}
.pi-lab{color:#c4a882;font-weight:500;margin-right:4px}
.upp{display:none;padding:0 16px 12px;text-align:center}
.upb{width:100%;padding:14px;background:#f0eee9;border:1px dashed #ccc;border-radius:12px;color:#666;font-size:14px;cursor:pointer;-webkit-tap-highlight-color:transparent;transition:all 0.2s}
.upb:active{background:#e5e2dd;transform:scale(0.97)}
.bot{position:fixed;bottom:0;left:0;right:0;background:rgba(255,255,255,0.82);-webkit-backdrop-filter:blur(16px);backdrop-filter:blur(16px);border-top:1px solid rgba(0,0,0,0.04);box-shadow:0 -4px 20px rgba(0,0,0,0.03);padding:6px 16px 20px;z-index:9999}
.brw{display:flex;align-items:center;justify-content:center;gap:10px}
.bn{flex:1;max-width:100px;min-height:48px;border-radius:12px;cursor:pointer;border:none;-webkit-tap-highlight-color:transparent;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:2px;padding:4px 0;transition:opacity 0.15s,transform 0.15s;user-select:none}
.bn:active{opacity:0.55;transform:scale(0.94)}
.bn-txt{font-size:10px;font-weight:500;line-height:1.2}
.bt1{background:transparent;border:1px solid #eee;color:#666}
.bt2{background:transparent;border:1px solid #eee;color:#666;position:relative}
.bt3{background:#1a1a1a;color:#fff}
.bt2.liked{color:#e74c3c!important;border-color:#f8d0d0!important}
.ft{font-size:12px;color:#c4a882;text-align:center;padding:3px 0 0;min-height:18px}
.fav-icon{display:inline-flex;align-items:center;justify-content:center}
.fav-icon-empty,.fav-icon-filled{display:inline-flex}
.fav-icon-filled{display:none}
.bt2.liked .fav-icon-empty{display:none}
.bt2.liked .fav-icon-filled{display:inline-flex}
</style>
<div class="pg">
  <div class="iv">
    <div class="fm">
      <img id="pi" src="" alt="" />
      <div class="fb" id="tb"><f-icon><svg width="22" height="30" viewBox="0 0 24 36" fill="none" xmlns="http://www.w3.org/2000/svg"><defs><linearGradient id="ngr" x1="12" y1="2" x2="12" y2="34" gradientUnits="userSpaceOnUse"><stop offset="0" stop-color="currentColor" stop-opacity="0.92"/><stop offset="0.18" stop-color="currentColor" stop-opacity="0.75"/><stop offset="0.5" stop-color="currentColor" stop-opacity="0.55"/><stop offset="0.75" stop-color="currentColor" stop-opacity="0.65"/><stop offset="1" stop-color="currentColor" stop-opacity="0.85"/></linearGradient></defs><path d="M12 2c-4 0-7 1.5-8 4.5C2.5 11 3 16 4 21s2 9.5 3 11c0.5 1 1.5 2 2 2.5 1 0.5 2 0.8 3 0.8s2-0.3 3-0.8c0.5-0.5 1.5-1.5 2-2.5 1-1.5 2-6 3-11s1.5-10 0-14.5C19 3.5 16 2 12 2z" fill="url(#ngr)"/><path d="M12 3.5c-3.2 0-5.5 1.2-6.5 3.8C4.2 10.5 4.8 15 5.8 20s2 8.5 2.8 10c0.5 0.8 1.2 1.5 1.7 1.8 0.7 0.4 1.5 0.6 2.5 0.6 1 0 1.8-0.2 2.5-0.6 0.5-0.3 1.2-1 1.7-1.8 0.8-1.5 1.8-5 2.8-10s1.6-9.5 0.6-12.7C17.5 4.7 15.2 3.5 12 3.5z" fill="currentColor" opacity="0.12"/><path d="M7 18c1.5-1 3.5-1.5 6-1.5s4.5 0.5 6 1.5" stroke="currentColor" stroke-width="0.5" opacity="0.12" stroke-linecap="round"/><path d="M7.5 22c1.5-0.8 3.5-1.2 5.5-1.2s4 0.4 5.5 1.2" stroke="currentColor" stroke-width="0.5" opacity="0.1" stroke-linecap="round"/><path d="M10.5 4c-0.5 0.5-0.8 1.2-1 2" stroke="currentColor" stroke-width="0.4" opacity="0.08" stroke-linecap="round"/><path d="M13.5 4c0.5 0.5 0.8 1.2 1 2" stroke="currentColor" stroke-width="0.4" opacity="0.08" stroke-linecap="round"/><path d="M9.5 5.5C8.5 7 8 9 8 11" stroke="currentColor" stroke-width="0.5" opacity="0.06" stroke-linecap="round"/><path d="M14.5 5.5C15.5 7 16 9 16 11" stroke="currentColor" stroke-width="0.5" opacity="0.06" stroke-linecap="round"/></svg></f-icon><span class="fb-txt">试戴</span></div>
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
  </div>
  <div class="ft" id="ft"></div>
</div>"""


_JS_CODE = """
() => {
    function _init() {
        var id = new URLSearchParams(window.location.search).get('from') || '';
        var num = id ? ('000' + id).slice(-3) : '';
        var deviceId = new URLSearchParams(window.location.search).get('device') || '';

        // Write device_id to hidden Gradio input
        if (deviceId) {
            var di = document.querySelector('#dev-inp input, #dev-inp textarea');
            if (di) { di.value = deviceId; }
        }
        window._tryonDeviceId = deviceId;

        // Set image
        var img = document.getElementById('pi');
        if (img && num) {
            img.src = '/gradio_api/file/raw_images/img_' + num + '.webp';
            img.onerror = function() {
                this.src = '/gradio_api/file/raw_images/img_' + num + '.jpg';
                this.onerror = function() {
                    this.src = '/gradio_api/file/raw_images/img_' + num + '.jpeg';
                };
            };
        }

        // Check favorite status - localStorage (fast) + server (authoritative)
        if (id) {
            try {
                var favs = JSON.parse(localStorage.getItem('nail_favs') || '[]');
                if (favs.indexOf(parseFloat(id)) !== -1) {
                    _setFav(true);
                }
            } catch(e) {}
            // Server check: ONLY upgrade to liked, never downgrade
            if (deviceId) {
                var xhr = new XMLHttpRequest();
                xhr.open('GET', '//' + window.location.hostname + ':7886/api/check_fav?device=' + encodeURIComponent(deviceId) + '&item=' + id, true);
                xhr.onload = function() {
                    try {
                        var r = JSON.parse(xhr.responseText);
                        if (r && r.data === '1') _setFav(true);
                    } catch(e) {}
                };
                xhr.send();
            }
        }
        function _setFav(liked) {
            var favBtn = document.getElementById('doFav');
            if (!favBtn) return;
            if (liked) {
                favBtn.classList.add('liked');
                favBtn._liked = true;
            } else {
                favBtn.classList.remove('liked');
                favBtn._liked = false;
            }
        }

        // Product info
        var _pd = _PROD_DATA;
        if (id && _pd[id]) {
            var p = _pd[id];
            if (p[0]) document.getElementById('pi-v1').textContent = p[0];
            if (p[1]) document.getElementById('pi-v2').textContent = p[1];
            if (p[2]) document.getElementById('pi-v3').textContent = p[2];
        }

        // File input
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
                document.getElementById('ft').textContent = '渲染中...';
                _renderLoading = true;
                // Send to render server
                var fd = new FormData();
                fd.append('file', file);
                fd.append('product_id', id || '0');
                var uid = deviceId || navigator.userAgent || 'web';
                fd.append('user_id', uid);
                fd.append('device_id', uid);
                var xhr = new XMLHttpRequest();
                xhr.open('POST', '//' + window.location.hostname + ':7887/render', true);
                xhr.onload = function() {
                    try {
                        var resp = JSON.parse(xhr.responseText);
                        if (resp.result) {
                            img.src = 'data:image/png;base64,' + resp.result;
                            var info = '';
                            if (resp.total_nails > 0) {
                                info = '检测到 ' + resp.total_nails + ' 个指甲，成功渲染 ' + resp.success_nails + ' 个';
                            }
                            document.getElementById('ft').textContent = info || '试戴完成';
                        } else if (resp.error) {
                            document.getElementById('ft').textContent = '渲染失败: ' + resp.error;
                        }
                    } catch(ex) {
                        document.getElementById('ft').textContent = '渲染出错';
                    }
                    _renderLoading = false;
                };
                xhr.onerror = function() {
                    document.getElementById('ft').textContent = '渲染服务未连接';
                    _renderLoading = false;
                };
                xhr.send(fd);
            }
        });

        // Particle spawner
        function _spawnParticles(el) {
            var rect = el.getBoundingClientRect();
            var cx = rect.left + rect.width / 2;
            var cy = rect.top + rect.height / 2;
            for (var i = 0; i < 10; i++) {
                var p = document.createElement('div');
                p.className = 'prt';
                var a = Math.random() * 360;
                var d = 40 + Math.random() * 60;
                p.style.setProperty('--px', Math.cos(a * Math.PI / 180) * d + 'px');
                p.style.setProperty('--py', Math.sin(a * Math.PI / 180) * d + 'px');
                p.style.left = cx + 'px';
                p.style.top = cy + 'px';
                p.style.background = ['#e74c3c','#ff6b6b','#ff4757','#c4a882'][Math.floor(Math.random() * 4)];
                document.body.appendChild(p);
                setTimeout(function(pt) { pt.remove(); }, 700, p);
            }
        }

        // Event handlers
        document.getElementById('tb').onclick = function() {
            try { navigator.vibrate(12); } catch(e) {}
            document.getElementById('us').style.display = '';
            fi.value = '';
            fi.click();
        };
        document.getElementById('upb').onclick = function() {
            fi.value = '';
            fi.click();
        };
        document.getElementById('goHome').onclick = function() {
            window.location.href = window.location.protocol + '//' + window.location.hostname + ':7860/';
        };
        document.getElementById('doFav').onclick = function() {
            if (!id) { document.getElementById('ft').textContent = '请先选择款式'; return; }
            if (this._liking) return;
            try { navigator.vibrate(15); } catch(e) {}
            var me = this;
            if (!me._liked) {
                var fi = me.querySelector('.fav-icon');
                if (fi) { fi.style.animation = 'none'; void fi.offsetWidth; fi.style.animation = 'favShake 0.3s ease-out'; }
            }
            function _saveFav(liked) {
                try {
                    var favs = JSON.parse(localStorage.getItem('nail_favs') || '[]');
                    var fid = parseFloat(id);
                    var idx = favs.indexOf(fid);
                    if (liked && idx === -1) favs.push(fid);
                    if (!liked && idx !== -1) favs.splice(idx, 1);
                    localStorage.setItem('nail_favs', JSON.stringify(favs));
                } catch(e) {}
            }
            if (me._liked) {
                // Unfavorite
                me._liking = true;
                fetch('/gradio_api/call/on_unfavorite', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({data: [parseFloat(id), window._tryonDeviceId || '']})
                }).then(function(r) { return r.json(); }).then(function(d) {
                    me.classList.remove('liked');
                    me._liked = false;
                    me._liking = false;
                    _saveFav(false);
                    document.getElementById('ft').textContent = '已取消';
                }).catch(function() {
                    me.classList.remove('liked');
                    me._liked = false;
                    me._liking = false;
                    _saveFav(false);
                    document.getElementById('ft').textContent = '已取消';
                });
            } else {
                // Favorite
                me._liking = true;
                _spawnParticles(me);
                setTimeout(function() { me.querySelector('.fav-icon').style.animation = ''; }, 1000);
                fetch('/gradio_api/call/on_favorite', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({data: [parseFloat(id), window._tryonDeviceId || '']})
                }).then(function(r) { return r.json(); }).then(function(d) {
                    me.classList.add('liked');
                    me._liked = true;
                    me._liking = false;
                    _saveFav(true);
                    document.getElementById('ft').textContent = '已收藏';
                }).catch(function() {
                    me.classList.add('liked');
                    me._liked = true;
                    me._liking = false;
                    _saveFav(true);
                    document.getElementById('ft').textContent = '已收藏';
                });
            }
        };
        document.getElementById('doShare').onclick = function() {
            document.getElementById('ft').textContent = '链接已复制';
        };
    }

    // Wait for DOM, then inject page
    function inject() {
        var app = document.querySelector('gradio-app');
        if (app && app.shadowRoot) {
            var container = app.shadowRoot.querySelector('.contain');
            if (container) container.style.display = 'none';
        }
        var root = document.getElementById('root') || document.querySelector('.gradio-container');
        if (root) {
            root.innerHTML = '';
            root.style.background = '#f8f7f4';
            root.style.maxWidth = '100vw';
            root.innerHTML = _PAGE_HTML;
            _init();
            return;
        }
        setTimeout(inject, 300);
    }
    inject();
}
"""


def create_interface():
    with gr.Blocks(css=CUSTOM_CSS, title="NAIL AI", head='<meta http-equiv="Cache-Control" content="no-cache,no-store,must-revalidate"><meta http-equiv="Pragma" content="no-cache"><meta name="viewport" content="width=device-width,initial-scale=1.0,maximum-scale=1.5,viewport-fit=cover"><style>html,body{margin:0!important;padding:0!important;overflow-x:hidden!important;width:100%!important;max-width:100vw!important}body{min-height:100dvh}gradio-app{display:block;width:100%!important;max-width:100vw!important;overflow-x:hidden!important}.contain,.main,.wrap,.container{max-width:100vw!important;width:100%!important;padding-left:0!important;padding-right:0!important;margin-left:0!important;margin-right:0!important}.gap,.form{max-width:100vw!important;width:100%!important;padding-left:0!important;padding-right:0!important}.progress-bar,.progress-text,.loading{display:none!important}</style>') as demo:
        sel_id = gr.Number(value=0, elem_id="sel-id", label="")
        fav_bt = gr.Button("收藏", elem_id="fav-bt")
        share_bt = gr.Button("分享", elem_id="share-bt")
        fb_bx = gr.Textbox(value="", elem_id="fb-bx", label="")
        unfav_bt = gr.Button("取消收藏", elem_id="unfav-bt")
        dev_inp = gr.Textbox(value="", elem_id="dev-inp", label="")
        chk_bx = gr.Textbox(value="", elem_id="chk-bx", label="")
        chk_bt = gr.Button("检查收藏", elem_id="chk-bt")

        fav_bt.click(fn=on_favorite, inputs=[sel_id, dev_inp], outputs=[fb_bx])
        share_bt.click(fn=on_share, inputs=[], outputs=[fb_bx])
        unfav_bt.click(fn=on_unfavorite, inputs=[sel_id, dev_inp], outputs=[fb_bx])
        chk_bt.click(fn=on_check_favorite, inputs=[sel_id, dev_inp], outputs=[chk_bx])

        prod_data = {}
        for iid, p in PRODUCT_CACHE.items():
            scene = p.get("scene_label", "").replace(",", " · ")
            skin = p.get("skin_label", "").replace(",", " · ")
            hand = p.get("hand_label", "").replace(",", " · ")
            prod_data[str(iid)] = [scene, skin, hand]

        # 替换顺序：先替换 _PROD_DATA，再替换 _PAGE_HTML
        # 因为 _PROD_DATA 在 JS 函数体内，必须在函数定义前替换
        js_final = _JS_CODE.replace("_PROD_DATA", json.dumps(prod_data, ensure_ascii=False))
        js_final = js_final.replace("_PAGE_HTML", json.dumps(_PAGE_HTML))

        demo.load(fn=None, js=js_final)

    from fastapi import Request
    @demo.app.get("/api/check_fav")
    async def api_check_fav(request: Request):
        device = request.query_params.get("device", "")
        item = request.query_params.get("item", "0")
        if not device or not item:
            return {"data": "0"}
        try:
            iid = int(item)
        except (ValueError, TypeError):
            return {"data": "0"}
        user = dm.get_or_create_user(device)
        uid = user["temp_user_id"]
        logs = dm.log.get_user_logs(uid)
        for log in reversed(logs):
            if int(log.get("item_id", 0)) == iid:
                op = log.get("operate_type", "")
                if op == "点击收藏":
                    return {"data": "1"}
                if op == "取消收藏":
                    return {"data": "0"}
        return {"data": "0"}

    return demo


if __name__ == "__main__":
    demo = create_interface()
    demo.launch(server_name="0.0.0.0", server_port=7885, share=False, allowed_paths=["raw_images"])
