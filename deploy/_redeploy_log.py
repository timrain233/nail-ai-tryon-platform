"""重新上传 log_manager.py"""
import base64, subprocess, os, time

BASE = os.path.dirname(os.path.abspath(__file__))
PLINK = os.path.join(BASE, "plink.exe")
PW = "Yrj20020906"
HOSTKEY = "SHA256:0OkXtQ4+0rYdeya7dVvoidOrn8auFdnPN6Vi/W0wHqo"

def upload_file(local, remote):
    with open(local, "rb") as f:
        data = f.read()
    b64 = base64.b64encode(data).decode()
    remote = remote.replace("\\", "/")
    proc = subprocess.Popen(
        [PLINK, "-hostkey", HOSTKEY, "-batch", "-pw", PW, "root@101.200.233.235",
         f"base64 -d > {remote}; echo OK"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    proc.stdin.write(b64.encode())
    proc.stdin.close()
    out = proc.stdout.read().decode("utf-8", errors="ignore")
    proc.wait()
    if "OK" in out:
        print(f"  OK: {local} ({len(data)//1024}KB)")
        return True
    print(f"  FAIL")
    return False

def run_remote(cmd):
    proc = subprocess.Popen(
        [PLINK, "-hostkey", HOSTKEY, "-batch", "-pw", PW, "root@101.200.233.235", cmd],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    out, _ = proc.communicate()
    return out.decode("utf-8", errors="ignore")

print("上传 log_manager.py ...")
upload_file(os.path.join(BASE, "nail_database/log_manager.py"),
            "/root/nail_app/nail_database/log_manager.py")

print("重启服务 ...")
run_remote("cd /root/nail_app && bash deploy.sh")

time.sleep(8)
print("健康检查 ...")
for port in [7860, 7885, 7886, 7887]:
    code = run_remote(f"curl -s -o /dev/null -w '%{{http_code}}' http://localhost:{port}/ --max-time 3").strip()
    print(f"  port {port}: {'✅' if code == '200' else code}")

print("完成! http://101.200.233.235:7860/")
