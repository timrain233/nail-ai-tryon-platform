"""
server.py - 美甲试戴 Flask 服务
================================
提供前端页面 + API接口
"""
import os
import sys
import io
import base64
import traceback
from flask import Flask, request, jsonify, send_file, render_template_string

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

BASE = os.path.dirname(os.path.abspath(__file__))
CUT3_DIR = os.path.join(BASE, "nail_cut3")
PRODUCT_DIR = os.path.join(BASE, "nail_product2")

app = Flask(__name__, static_folder=BASE, static_url_path="/static")


# ── 首页 ──────────────────────────────────────────
@app.route("/")
def index():
    html_path = os.path.join(BASE, "tests", "tryon.html")
    if os.path.exists(html_path):
        with open(html_path, "r", encoding="utf-8") as f:
            return f.read()
    return "<h1>tryon.html not found</h1>"


# ── API: 商品列表 ──────────────────────────────────
@app.route("/api/products")
def api_products():
    products = []
    if os.path.exists(CUT3_DIR):
        for d in sorted(os.listdir(CUT3_DIR)):
            dp = os.path.join(CUT3_DIR, d)
            if os.path.isdir(dp):
                # 统计该商品有多少指甲
                nails = len([f for f in os.listdir(dp) if f.endswith(".png")])
                preview = f"/api/preview/{d}"
                products.append({
                    "id": d,
                    "name": d.replace("img_", "款式 "),
                    "nails": nails,
                    "preview": preview,
                })
    return jsonify(products)


# ── API: 商品预览图 ───────────────────────────────
@app.route("/api/preview/<product_id>")
def api_preview(product_id):
    path = os.path.join(PRODUCT_DIR, product_id, "preview.jpg")
    if os.path.exists(path):
        return send_file(path, mimetype="image/jpeg")
    return jsonify({"error": "not found"}), 404


# ── API: 一键试戴 ─────────────────────────────────
@app.route("/api/tryon", methods=["POST"])
def api_tryon():
    try:
        # 获取上传图片
        if "image" not in request.files:
            return jsonify({"error": "未上传图片"}), 400
        file = request.files["image"]
        product_id = request.form.get("product_id", "").strip()

        if not product_id:
            return jsonify({"error": "未选择商品"}), 400

        # 验证商品存在
        product_path = os.path.join(CUT3_DIR, product_id)
        if not os.path.isdir(product_path):
            return jsonify({"error": f"商品 {product_id} 不存在"}), 404

        # 读取图片
        import numpy as np
        import cv2
        img_bytes = file.read()
        nparr = np.frombuffer(img_bytes, np.uint8)
        bgr = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if bgr is None:
            return jsonify({"error": "图片格式不支持"}), 400
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)

        # 调用试戴引擎
        from core.nail_tryon import process_tryon_from_array
        result_rgb, missing = process_tryon_from_array(rgb, product_id)

        # 编码结果
        result_bgr = cv2.cvtColor(result_rgb, cv2.COLOR_RGB2BGR)
        _, buf = cv2.imencode(".jpg", result_bgr, [cv2.IMWRITE_JPEG_QUALITY, 95])
        result_b64 = base64.b64encode(buf).decode("utf-8")

        return jsonify({
            "success": True,
            "result": f"data:image/jpeg;base64,{result_b64}",
            "missing": missing,
        })

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


# ── 启动 ──────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=5000, help="服务端口")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="绑定地址")
    args = parser.parse_args()

    print(f"\n{'='*50}")
    print(f"  美甲试戴服务已启动")
    print(f"  地址: http://localhost:{args.port}")
    print(f"{'='*50}\n")
    app.run(host=args.host, port=args.port, debug=False)