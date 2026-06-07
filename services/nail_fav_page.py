"""
nail_fav_page.py - FastAPI 收藏与试戴记录页面
修复：_REQ_DONE 全局锁 / 兼容新旧CSV / tryon_results目录兜底
"""
import sys, os, csv, asyncio

_PROJ_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, _PROJ_ROOT)
from nail_database import dm

# 试戴历史记录持久化
from database.tryon_records_manager import read_tryon_records

PORT = 7886
dm.initialize()
ALL_PRODUCTS = dm.product.read_all()
PRODUCT_MAP = {str(p["item_id"]): p for p in ALL_PRODUCTS}

BASE_DIR = _PROJ_ROOT
DB_DIR = os.path.join(BASE_DIR, "nail_database")
CUT3_DIR = os.path.join(BASE_DIR, "assets", "nail_cut3")
NEW_LOG_CSV = os.path.join(DB_DIR, "tryon_behavior_log.csv")
OLD_LOG_CSV = os.path.join(DB_DIR, "user_behavior_log.csv")
RESULTS_DIR = os.path.join(DB_DIR, "tryon_results")
TEMP_USER_CSV = os.path.join(DB_DIR, "temp_user.csv")

# 兜底创建 tryon_results/
os.makedirs(RESULTS_DIR, exist_ok=True)


def _append_old_record(action, pid, tm, favs, tryons, seen_fav, seen_tryon):
    """辅助：将旧CSV的一行记录追加到对应列表（去重）。"""
    p = PRODUCT_MAP.get(pid, {})
    if action == "favorite" and pid not in seen_fav:
        seen_fav.add(pid)
        favs.append({
            "item_id": pid,
            "item_name": p.get("item_name", ""),
            "scene": p.get("scene_label", "").replace(",", " · "),
            "hand": p.get("hand_label", "").replace(",", " · "),
            "time": tm,
        })
    elif action == "unfavorite" and pid in seen_fav:
        seen_fav.discard(pid)
        for i, r in enumerate(favs):
            if r["item_id"] == pid:
                favs.pop(i)
                break
    if action == "tryon" and pid not in seen_tryon:
        seen_tryon.add(pid)
        tryons.append({
            "item_id": pid,
            "item_name": p.get("item_name", ""),
            "scene": p.get("scene_label", "").replace(",", " · "),
            "hand": p.get("hand_label", "").replace(",", " · "),
            "image": "",
            "time": tm,
        })


# 旧 CSV 操作类型映射
_OLD_ACTION_MAP = {
    "点击收藏": "favorite",
    "取消收藏": "unfavorite",
    "开始试戴": "tryon",
}


def _read_device_uid_sync(device):
    """通过 device_id 在 temp_user.csv 中查找旧 temp_user_id"""
    if not device or not os.path.exists(TEMP_USER_CSV):
        return ""
    try:
        with open(TEMP_USER_CSV, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if (row.get("device_id") or "").strip() == device:
                    return (row.get("temp_user_id") or "").strip()
    except Exception:
        pass
    return ""


def _read_old_csv_sync(old_uid):
    """读取旧版 user_behavior_log.csv，严格按 old_uid 过滤，无回退全表"""
    if not old_uid or not os.path.exists(OLD_LOG_CSV):
        return [], []
    favs, tryons = [], []
    seen_fav, seen_tryon = set(), set()
    try:
        with open(OLD_LOG_CSV, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                ruid = (row.get("temp_user_id") or "").strip()
                if ruid != old_uid:
                    continue
                otype = (row.get("operate_type") or "").strip()
                iid_str = (row.get("item_id") or "").strip()
                if not iid_str or iid_str == "0":
                    continue
                pid = str(int(float(iid_str)))
                action = _OLD_ACTION_MAP.get(otype, "")
                tm = (row.get("operate_time") or "").strip()
                _append_old_record(action, pid, tm, favs, tryons, seen_fav, seen_tryon)
    except Exception:
        pass
    return favs, tryons


def _read_new_csv_sync(uid):
    """读取新版 tryon_behavior_log.csv，按 nail_uid 过滤"""
    if not uid or not os.path.exists(NEW_LOG_CSV):
        return [], []
    favs, tryons = [], []
    seen_fav, seen_tryon = set(), set()
    with open(NEW_LOG_CSV, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            r_uid = (row.get("user_id") or "").strip()
            if r_uid != uid:
                continue
            pid = (row.get("product_id") or "").strip()
            action = (row.get("action") or "").strip()
            if not pid or pid == "0":
                continue
            if action == "favorite" and pid not in seen_fav:
                seen_fav.add(pid)
                p = PRODUCT_MAP.get(pid, {})
                favs.append({
                    "item_id": pid,
                    "item_name": p.get("item_name", ""),
                    "scene": p.get("scene_label", "").replace(",", " · "),
                    "hand": p.get("hand_label", "").replace(",", " · "),
                    "time": row.get("time", ""),
                })
            elif action == "unfavorite" and pid in seen_fav:
                seen_fav.discard(pid)
                favs = [r for r in favs if r["item_id"] != pid]
            if action in ("tryon", "start_tryon") and row.get("success", "0") == "1" and pid not in seen_tryon:
                seen_tryon.add(pid)
                p = PRODUCT_MAP.get(pid, {})
                tryons.append({
                    "item_id": pid,
                    "item_name": p.get("item_name", ""),
                    "scene": p.get("scene_label", "").replace(",", " · "),
                    "hand": p.get("hand_label", "").replace(",", " · "),
                    "image": "",
                    "time": row.get("time", ""),
                })
    # 补 tryon_results 目录图片
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in uid)
    ud = os.path.join(RESULTS_DIR, safe)
    if os.path.isdir(ud):
        for fname in sorted(os.listdir(ud), reverse=True)[:50]:
            if not fname.endswith(".png"):
                continue
            pid = fname.split("_")[0] if "_" in fname else fname.replace(".png", "")
            if pid not in seen_tryon:
                seen_tryon.add(pid)
                p = PRODUCT_MAP.get(pid, {})
                tryons.append({
                    "item_id": pid,
                    "item_name": p.get("item_name", ""),
                    "scene": p.get("scene_label", "").replace(",", " · "),
                    "hand": p.get("hand_label", "").replace(",", " · "),
                    "image": f"/tryon_results/{safe}/{fname}",
                    "time": fname.replace(".png", "").replace("_", " "),
                })
    return favs, tryons


def _merge_by_latest_time(items, key="item_id", time_key="time"):
    """
    合并去重：同一 key(item_id) 保留 time 最新的一条，返回按 time 倒序列表
    """
    best = {}
    for item in items:
        k = item.get(key)
        if not k:
            continue
        t = item.get(time_key, "") or ""
        existing = best.get(k)
        if existing is None or t > (existing.get(time_key, "") or ""):
            best[k] = item
    return sorted(best.values(), key=lambda x: x.get(time_key, "") or "", reverse=True)


def _read_csv_by_uid_sync(uid, device=""):
    """
    统一入口：分层读取 新CSV(按nail_uid) + 旧CSV(按device映射的旧ID)，
    合并去重（同item_id保留时间最新），按时间倒序返回 (favs, tryons)
    """
    all_favs = []
    all_tryons = []

    # ── ① 数据源1：新日志 tryon_behavior_log.csv ──
    try:
        nf, nt = _read_new_csv_sync(uid) if uid else ([], [])
        print(f"[fav_list] 新CSV uid={uid} → favs={len(nf)} tryons={len(nt)}")
        all_favs.extend(nf)
        all_tryons.extend(nt)
    except Exception as e:
        print(f"[fav_list] 新CSV异常: {e}")

    # ── ② ID映射：device → temp_user.csv → 旧temp_user_id ──
    old_uid = ""
    if device:
        try:
            old_uid = _read_device_uid_sync(device)
            print(f"[fav_list] device={device} → old_uid={old_uid!r}")
        except Exception as e:
            print(f"[fav_list] device映射异常: {e}")
    if not old_uid and uid:
        old_uid = uid  # 兼容 ?uid=20260601-xxxx 直接传老ID

    # ── ③ 数据源2：旧日志 user_behavior_log.csv ──
    if old_uid:
        try:
            of, ot = _read_old_csv_sync(old_uid)
            print(f"[fav_list] 旧CSV old_uid={old_uid} → favs={len(of)} tryons={len(ot)}")
            all_favs.extend(of)
            all_tryons.extend(ot)
        except Exception as e:
            print(f"[fav_list] 旧CSV异常: {e}")

    # ── ④ 数据源3：专用试戴记录 database/tryon_records.csv ──
    if uid:
        try:
            tr = read_tryon_records(uid)
            if tr:
                print(f"[fav_list] 新CSV tryon_records uid={uid} → {len(tr)}条")
                for r in tr:
                    pid = r["item_id"]
                    p = PRODUCT_MAP.get(pid, {})
                    r["item_name"] = p.get("item_name", "")
                    r["scene"] = p.get("scene_label", "").replace(",", " · ")
                    r["hand"] = p.get("hand_label", "").replace(",", " · ")
                all_tryons.extend(tr)
        except Exception as e:
            print(f"[fav_list] tryon_records异常: {e}")

    # ── ⑤ 合并去重：同item_id保留时间最新，按时间倒序 ──
    favs = _merge_by_latest_time(all_favs)
    tryons = _merge_by_latest_time(all_tryons)

    print(f"[fav_list] 合并后 → favs={len(favs)} tryons={len(tryons)}")
    return favs, tryons


_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0,maximum-scale=1.5,viewport-fit=cover">
<meta http-equiv="Cache-Control" content="no-cache,no-store,must-revalidate">
<meta http-equiv="Pragma" content="no-cache">
<meta http-equiv="Expires" content="0">
<title>我的收藏与试戴 - NAIL AI</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,'PingFang SC','Microsoft YaHei',sans-serif;background:#f8f7f4;min-height:100vh}
@keyframes _spin{100%{transform:rotate(360deg)}}
.pg{min-height:100vh;background:#f8f7f4;padding-bottom:80px;background-image:radial-gradient(ellipse at 50% 0%,rgba(196,168,130,0.05) 0%,transparent 70%)}
.hd{display:flex;align-items:center;gap:8px;padding:14px 12px 8px;position:sticky;top:0;z-index:10;background:rgba(248,247,244,0.92);-webkit-backdrop-filter:blur(12px);backdrop-filter:blur(12px);border-bottom:1px solid rgba(0,0,0,0.03)}
.bk{background:none;border:none;cursor:pointer;padding:4px;color:#666;display:flex;align-items:center;-webkit-tap-highlight-color:transparent;border-radius:50%;width:36px;height:36px;justify-content:center}
.bk:active{background:rgba(0,0,0,0.05)}
.tt{font-size:16px;font-weight:600;color:#1a1a1a;flex:1;text-align:center;letter-spacing:0.02em}
.tabs{display:flex;margin:12px 16px;border-radius:12px;background:#f0eeeb;overflow:hidden;padding:3px}
.tb{flex:1;text-align:center;padding:8px 0;font-size:13px;color:#888;cursor:pointer;border-radius:9px;transition:all 0.25s;-webkit-tap-highlight-color:transparent;font-weight:500;user-select:none}
.tb.active{background:#fff;color:#c4a882;box-shadow:0 1px 6px rgba(0,0,0,0.06);font-weight:600}
.em{text-align:center;padding:60px 20px;color:#bbb;font-size:13px;background:rgba(196,168,130,0.04);border:0.5px solid rgba(196,168,130,0.08);border-radius:16px;max-width:260px;margin:80px auto}
.gw{display:grid;grid-template-columns:repeat(2,1fr);gap:16px;padding:0 16px 80px}
.gc{border-radius:12px;overflow:hidden;background:#fff;box-shadow:0 1px 3px rgba(0,0,0,0.04),0 8px 32px rgba(196,168,130,0.08);display:flex;flex-direction:column;border:1px solid rgba(255,255,255,0.5);transition:transform 0.25s cubic-bezier(.22,1,.36,1),box-shadow 0.25s ease}
.gc:hover{transform:translateY(-3px);box-shadow:0 4px 12px rgba(0,0,0,0.06),0 12px 48px rgba(196,168,130,0.14)}
.gc-img{position:relative;width:100%;aspect-ratio:1/1;overflow:hidden;background:#f0eeeb}
.gc-img img{width:100%;height:100%;object-fit:cover;display:block}
.gc-info{padding:8px 12px;background:#fff;min-height:64px;border-top:1px solid rgba(196,168,130,0.3)}
.gc-name{font-size:13px;color:#2a2a2a;font-weight:400;line-height:1.35;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.gc-tags{font-size:10px;color:#999;padding-top:3px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.tryon-c{display:flex;gap:12px;padding:12px 16px;background:#fff;margin:0 16px 12px;border-radius:12px;box-shadow:0 1px 3px rgba(0,0,0,0.04);align-items:center;border:1px solid rgba(255,255,255,0.5)}
.tryon-i{width:80px;height:80px;flex-shrink:0;border-radius:8px;overflow:hidden;background:#f0eeeb}
.tryon-i img{width:100%;height:100%;object-fit:cover;display:block}
.tryon-d{flex:1;min-width:0}
.tryon-n{font-size:13px;color:#2a2a2a;font-weight:500}
.tryon-t{font-size:10px;color:#999;padding-top:3px}
.sp{text-align:center;padding:80px 20px;color:#ccc;font-size:13px}
.sp-svg{animation:_spin 0.8s linear infinite;margin-bottom:8px}
#loading{position:fixed;top:0;left:0;right:0;z-index:999;height:2px;background:linear-gradient(90deg,#c4a882 30%,#e8d5c0 50%,#c4a882 70%);background-size:200% 100%;animation:loadBar 1.2s ease infinite;display:none}
@keyframes loadBar{0%{transform:translateX(-100%)}100%{transform:translateX(100%)}}
</style>
</head>
<body>
<div id="loading"></div>
<div class="pg">
  <div class="hd">
    <button class="bk" onclick="window.location.href=window.location.protocol+'//'+window.location.hostname+':7860/'"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 12H5"/><path d="M12 19l-7-7 7-7"/></svg></button>
    <span class="tt">我的收藏与试戴</span>
  </div>
  <div class="tabs">
    <span class="tb active" id="t0"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px;margin-right:3px"><path d="M19 14c1.49-1.46 3-3.21 3-5.5A5.5 5.5 0 0 0 16.5 3c-1.76 0-3 .5-4.5 2-1.5-1.5-2.74-2-4.5-2A5.5 5.5 0 0 0 2 8.5c0 2.3 1.5 4.05 3 5.5l7 7Z"/></svg>收藏</span>
    <span class="tb" id="t1"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px;margin-right:3px"><path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/><circle cx="12" cy="13" r="4"/></svg>试戴记录</span>
  </div>
  <div id="b0"><div class="sp"><svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="sp-svg"><path d="M21 12a9 9 0 1 1-6.219-8.56"/></svg><div>加载中...</div></div></div>
  <div id="b1" style="display:none"><div class="sp"><svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="sp-svg"><path d="M21 12a9 9 0 1 1-6.219-8.56"/></svg><div>加载中...</div></div></div>
</div>
<script>
(function(){
var _favLoaded = false;
var _tryonLoaded = false;

// 兜底：5秒后无论如何隐藏所有 loading
setTimeout(function(){
  var ld = document.getElementById('loading');
  if(ld) ld.style.display = 'none';
  var b0 = document.getElementById('b0');
  var b1 = document.getElementById('b1');
  if(b0 && b0.querySelector('.sp') && !_favLoaded){
    b0.innerHTML = '<div class="em">暂无记录</div>';
  }
  if(b1 && b1.querySelector('.sp') && !_tryonLoaded){
    b1.innerHTML = '<div class="em">暂无记录</div>';
  }
}, 5000);

function _gp(n) {
  try {
    var s = new URLSearchParams(window.location.search);
    return s.get(n) || '';
  } catch(e) { return ''; }
}

function _ls(key) {
  try { return localStorage.getItem(key); } catch(e) { return null; }
}
function _lss(key, val) {
  try { localStorage.setItem(key, val); } catch(e) {}
}
function getUid() {
  try {
    var u = new URLSearchParams(window.location.search).get('uid');
    if (u && u.length > 0) { _lss('nail_uid', u); return u; }
  } catch(e) {}
  return _ls('nail_uid') || '';
}

document.addEventListener('DOMContentLoaded', function() {
  try {
    var uid = getUid();
    if (!uid) {
      uid = 'u' + Date.now().toString(36) + Math.random().toString(36).slice(2, 8);
      _lss('nail_uid', uid);
    }
    var dev = _gp('device');
    loadFavs(uid, dev);
  } catch(e) {
    console.error('[fav] init err:', e);
    var b0 = document.getElementById('b0');
    if(b0) b0.innerHTML = '<div class="em">加载失败，请刷新重试</div>';
  }
});

function loadFavs(uid, dev) {
  if (_favLoaded) return;
  _favLoaded = true;
  var sp = document.querySelector('#b0 .sp');
  if (sp) sp.style.display = '';
  var loadEl = document.getElementById('loading');
  loadEl.style.display = 'block';
  var url = '/api/fav_list?uid=' + encodeURIComponent(uid);
  if (dev) url += '&device=' + encodeURIComponent(dev);
  req(url).then(function(d) {
    renderFavs((d && d.list) || []);
  });
}

document.getElementById('t1').onclick = function() {
  var all = document.querySelectorAll('.tb');
  for (var i = 0; i < all.length; i++) all[i].classList.remove('active');
  this.classList.add('active');
  document.getElementById('b1').style.display = '';
  document.getElementById('b0').style.display = 'none';
  if (_tryonLoaded) return;
  _tryonLoaded = true;
  var sp = document.querySelector('#b1 .sp');
  if (sp) sp.style.display = '';
  var uid = getUid();
  if (!uid) { document.getElementById('b1').innerHTML = '<div class="em">暂无记录</div>'; return; }
  var dev = _gp('device');
  var loadEl = document.getElementById('loading');
  loadEl.style.display = 'block';
  var url = '/api/tryon_list?uid=' + encodeURIComponent(uid);
  if (dev) url += '&device=' + encodeURIComponent(dev);
  req(url).then(function(d) {
    renderTryons((d && d.list) || []);
  });
};

document.getElementById('t0').onclick = function() {
  var all = document.querySelectorAll('.tb');
  for (var i = 0; i < all.length; i++) all[i].classList.remove('active');
  this.classList.add('active');
  document.getElementById('b0').style.display = '';
  document.getElementById('b1').style.display = 'none';
};

async function req(url) {
  var ctrl = new AbortController();
  var tid = setTimeout(function(){ ctrl.abort(); }, 3000);
  var loadEl = document.getElementById('loading');
  try {
    var res = await fetch(url, {signal: ctrl.signal});
    if (!res.ok) throw new Error('http err');
    return await res.json();
  } catch(e) {
    return {list: []};
  } finally {
    clearTimeout(tid);
    setTimeout(function(){ if(loadEl) loadEl.style.display = 'none'; }, 500);
  }
}

function renderFavs(list) {
  var box = document.getElementById('b0');
  if (!list || list.length === 0) { box.innerHTML = '<div class="em">暂无收藏</div>'; return; }
  var h = '<div class="gw">';
  for (var i = 0; i < list.length; i++) {
    var it = list[i];
    var n = ('000' + it.item_id).slice(-3);
    var t = it.scene || '';
    if (it.hand && t.indexOf(it.hand) === -1) t = t ? t + ' \u00B7 ' + it.hand : it.hand;
    h += '<div class="gc"><div class="gc-img"><img src="/raw_images/img_' + n + '.webp" alt="' + (it.item_name || '') + '" loading="lazy" onerror="this.style.display=\\'none\\'" /></div><div class="gc-info"><div class="gc-name">' + (it.item_name || '') + '</div><div class="gc-tags">' + t + '</div></div></div>';
  }
  h += '</div>';
  box.innerHTML = h;
}

function renderTryons(list) {
  var box = document.getElementById('b1');
  if (!list || list.length === 0) { box.innerHTML = '<div class="em">暂无试戴记录</div>'; return; }
  var h = '<div class="gw">';
  for (var i = 0; i < list.length; i++) {
    var it = list[i];
    var n = ('000' + it.item_id).slice(-3);
    var t = it.scene || '';
    if (it.hand && t.indexOf(it.hand) === -1) t = t ? t + ' \u00B7 ' + it.hand : it.hand;
    var imgSrc = it.image || '/raw_images/img_' + n + '.webp';
    h += '<div class="gc"><div class="gc-img"><img src="' + imgSrc + '" alt="' + (it.item_name || '') + '" loading="lazy" onerror="this.style.display=\\'none\\'" /></div><div class="gc-info"><div class="gc-name">' + (it.item_name || '') + '</div><div class="gc-tags">' + t + '</div></div></div>';
  }
  h += '</div>';
  box.innerHTML = h;
}
})();
</script>
</body>
</html>"""


if __name__ == "__main__":
    from core.startup_check import check_all, print_report
    _sr = check_all()
    print_report(_sr, "nail_fav_page")

    import uvicorn
    from fastapi import FastAPI, Request
    from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
    from fastapi.staticfiles import StaticFiles
    from fastapi.middleware.cors import CORSMiddleware

    app = FastAPI(title="NAIL AI - 收藏与试戴记录")
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

    if os.path.isdir(CUT3_DIR):
        app.mount("/nail_cut3", StaticFiles(directory=CUT3_DIR), name="nail_cut3")
        app.mount("/raw_image", StaticFiles(directory=CUT3_DIR), name="nail_cut3_raw")
    if os.path.isdir(RESULTS_DIR):
        app.mount("/tryon_results", StaticFiles(directory=RESULTS_DIR), name="tryon_results")
    if os.path.isdir(os.path.join(BASE_DIR, "assets", "raw_images")):
        app.mount("/raw_images", StaticFiles(directory=os.path.join(BASE_DIR, "assets", "raw_images")), name="raw_images")

    @app.get("/")
    async def index():
        return HTMLResponse(
            content=_HTML,
            headers={"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache", "Expires": "0"}
        )

    @app.get("/go_home")
    async def go_home(request: Request):
        host = request.headers.get("host", f"localhost:7860")
        return RedirectResponse(url=f"http://{host.split(':')[0]}:7860/")

    @app.get("/api/fav_list")
    async def api_fav_list(uid: str = "", device: str = ""):
        if not uid:
            return {"list": []}
        try:
            favs, _ = await asyncio.to_thread(_read_csv_by_uid_sync, uid, device)
            return {"list": favs}
        except Exception as e:
            print(f"[fav_list] err: {e}")
            return {"list": []}

    @app.get("/api/tryon_list")
    async def api_tryon_list(uid: str = "", device: str = ""):
        if not uid:
            return {"list": []}
        try:
            _, tryons = await asyncio.to_thread(_read_csv_by_uid_sync, uid, device)
            return {"list": tryons}
        except Exception as e:
            print(f"[tryon_list] err: {e}")
            return {"list": []}

    # 兼容
    @app.get("/api/tryon_check_fav")
    async def tryon_check_fav(uid: str = "", device: str = ""):
        if not uid:
            return {"favs": [], "tryons": []}
        try:
            favs, tryons = await asyncio.to_thread(_read_csv_by_uid_sync, uid, device)
            return {"favs": favs, "tryons": tryons}
        except Exception:
            return {"favs": [], "tryons": []}

    print(f"[nail_fav_page] 启动 http://0.0.0.0:{PORT}/")
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")