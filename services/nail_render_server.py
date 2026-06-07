"""
nail_render_server.py - 美甲试戴渲染服务（使用预处理钉切图）
接受上传手部照片 + 商品ID → 返回渲染结果（base64 PNG）

增强: 异常隔离 + 640px缩放 + 运营日志 + 试戴图保存
"""
import sys, os, json, io, base64, traceback, time, hashlib

_PROJ = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, _PROJ)

# 日志模块
from nail_database.log_manager import (
    log_behavior, log_tryon_debug, update_heat_report
)

# 试戴历史记录持久化
from database.tryon_records_manager import append_tryon_record

# 试戴质量评分
from core.nail_quality_check import compute_scores

# 试戴结果保存目录
RESULTS_DIR = os.path.join(_PROJ,
                           "nail_database", "tryon_results")

_renderer = None

def _get_renderer():
    global _renderer
    if _renderer is None:
        from core.nail_renderer import NailTryOnRenderer
        _renderer = NailTryOnRenderer()
    return _renderer


def _sanitize_filename(s):
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in s)


def _save_tryon_result(result_img, user_id, product_id):
    """保存试戴结果图到磁盘"""
    try:
        result_dir = os.path.join(RESULTS_DIR, _sanitize_filename(user_id))
        os.makedirs(result_dir, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        fname = f"{product_id}_{ts}.png"
        fpath = os.path.join(result_dir, fname)
        from PIL import Image
        Image.fromarray(result_img).save(fpath, "PNG")
        return f"{user_id}/{fname}"
    except Exception:
        return None


PORT = 7887

if __name__ == "__main__":
    from core.startup_check import check_all, print_report
    from core.log_rotator import rotate_log_file

    _sr = check_all()
    print_report(_sr, "nail_render_server")
    rotate_log_file(os.path.join(_PROJ, "logs", "log_render.log"))

    from fastapi import FastAPI, File, UploadFile, Form
    from fastapi.responses import JSONResponse
    from fastapi.middleware.cors import CORSMiddleware
    import uvicorn
    import numpy as np
    from PIL import Image

    app = FastAPI(title="NAIL AI 试戴渲染")
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

    @app.on_event("startup")
    async def startup():
        print("[Render] 预热渲染器...")
        t0 = time.time()
        r = _get_renderer()
        print(f"[Render] 渲染器就绪: mode={r.segmentor.mode}, 耗时{time.time()-t0:.1f}s")

    @app.post("/render")
    async def render_endpoint(file: UploadFile = File(...), product_id: str = Form("0"),
                               user_id: str = Form(""), device_id: str = ""):
        start = time.time()
        pid = str(product_id).strip()
        try:
            try:
                contents = await file.read()
                pil_img = Image.open(io.BytesIO(contents)).convert("RGB")
                hand_photo = np.array(pil_img)
            except Exception:
                log_behavior(user_id, pid, "tryon", False, 0, device_id)
                return JSONResponse(status_code=400, content={"error": "图片读取失败", "success": False})

            print(f"[Render] 收到图片: {hand_photo.shape}, 商品ID={pid}")

            renderer = _get_renderer()

            result_img, nail_results = renderer.render(
                hand_photo,
                product_rgb=None,
                product_id=pid
            )

            elapsed = time.time() - start
            total = len(nail_results)
            success_count = sum(1 for r in nail_results if r.get("success", False))

            # 逐指日志
            for nr in nail_results:
                fid = nr.get("finger", -1)
                log_tryon_debug(
                    finger_id=fid,
                    product_id=pid,
                    unet_detected=True,
                    corners_ok=(nr.get("corners") is not None if "corners" in nr else True),
                    render_success=nr.get("success", False),
                    error_msg=nr.get("error", "")
                )

            # 用户行为日志
            log_behavior(user_id, pid, "tryon", success_count > 0, elapsed, device_id)

            # 热度报表更新
            update_heat_report(pid)

            # 自动评分（后台静默执行，异常兜底）
            fit_score = ""
            quality_score = ""
            try:
                fs, qs = compute_scores(nail_results, pid)
                fit_score = str(fs)
                quality_score = str(qs)
                print(f"[Render] 评分: fit={fit_score} quality={quality_score}")
            except Exception as e:
                print(f"[Render] 评分异常(已忽略): {e}")

            # 保存试戴结果图到磁盘
            result_path = _save_tryon_result(result_img, user_id, pid)
            if result_path:
                print(f"[Render] 试戴图已保存: {result_path}")
                raw_img_url = f"/raw_images/img_{str(pid).zfill(3)}.webp"
                result_img_url = f"/tryon_results/{result_path}"
                ts = time.strftime("%Y-%m-%d %H:%M:%S")
                append_tryon_record(user_id, pid, raw_img_url, result_img_url, ts, fit_score, quality_score)

            result_pil = Image.fromarray(result_img)
            buf = io.BytesIO()
            result_pil.save(buf, format="PNG")
            b64 = base64.b64encode(buf.getvalue()).decode()

            print(f"[Render] 完成: {total}甲, {success_count}成功, {elapsed:.1f}s")

            return JSONResponse(content={
                "result": b64, "format": "png",
                "total_nails": total, "success_nails": success_count,
                "elapsed_sec": round(elapsed, 2),
                "success": True,
                "nail_results": nail_results
            })
        except Exception as e:
            elapsed = time.time() - start
            log_behavior(user_id, pid, "tryon", False, elapsed, device_id)
            traceback.print_exc()
            return JSONResponse(content={
                "error": str(e), "success": False,
                "elapsed_sec": round(elapsed, 2)
            })

    @app.post("/log_click")
    async def log_click_endpoint(product_id: str = Form("0"), user_id: str = Form(""),
                                   device_id: str = Form("")):
        """记录商品点击/收藏"""
        action = "click"
        log_behavior(user_id, product_id, action, True, 0, device_id)
        update_heat_report(product_id)
        return JSONResponse(content={"success": True})

    @app.post("/log_favorite")
    async def log_favorite_endpoint(product_id: str = Form("0"), user_id: str = Form(""),
                                      device_id: str = Form("")):
        log_behavior(user_id, product_id, "favorite", True, 0, device_id)
        update_heat_report(product_id)
        return JSONResponse(content={"success": True})

    @app.get("/heat_report")
    async def get_heat_report():
        from nail_database.log_manager import get_heat_report
        return JSONResponse(content={"success": True, "data": get_heat_report()})

    @app.get("/health")
    async def health():
        return {"status": "ok", "port": PORT, "renderer_ready": _renderer is not None}

    print(f"渲染服务启动在端口 {PORT}")
    uvicorn.run(app, host="0.0.0.0", port=PORT)
