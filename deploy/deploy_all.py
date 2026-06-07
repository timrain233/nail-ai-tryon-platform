"""
deploy_all.py - 一键部署 nail_cut3 + 代码到远程服务器
"""
import base64, subprocess, sys, os, tarfile, io, shutil

HOSTKEY = "SHA256:0OkXtQ4+0rYdeya7dVvoidOrn8auFdnPN6Vi/W0wHqo"
PW = "Yrj20020906"
PLINK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "plink.exe")
BASE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.abspath(os.path.join(BASE, ".."))

def upload_file(local, remote):
    """上传单个文件"""
    if not os.path.exists(local):
        print(f"  SKIP: {local} not found")
        return False
    with open(local, "rb") as f:
        data = f.read()
    b64 = base64.b64encode(data).decode()
    remote = remote.replace('\\', '/')
    proc = subprocess.Popen(
        [PLINK, "-hostkey", HOSTKEY, "-batch", "-pw", PW, "root@101.200.233.235",
         f"base64 -d > {remote}; echo OK"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    proc.stdin.write(b64.encode())
    proc.stdin.close()
    stdout_data = proc.stdout.read().decode("utf-8", errors="ignore")
    proc.wait()
    if "OK" in stdout_data:
        size_kb = len(data) / 1024
        print(f"  OK: {local} -> {remote} ({size_kb:.1f} KB)")
        return True
    else:
        print(f"  FAIL: {local} -> {remote}")
        return False

def upload_tar_to_dir(local_dir, remote_dir):
    """将本地目录打包为tar.gz并上传到远程解压"""
    print(f"  Packing {local_dir}/ ...")
    buf = io.BytesIO()
    # tar.gz
    base_name = os.path.basename(local_dir)
    parent = os.path.dirname(local_dir)
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        tar.add(local_dir, arcname=base_name)
    tar_data = buf.getvalue()
    tar_size_kb = len(tar_data) / 1024
    print(f"  Tar size: {tar_size_kb:.0f} KB")
    b64 = base64.b64encode(tar_data).decode()

    rm_cmd = f"rm -rf {remote_dir}_old 2>/dev/null; mv {remote_dir} {remote_dir}_old 2>/dev/null;"
    extract = f"base64 -d | tar -xzf - -C {os.path.dirname(remote_dir)}; echo OK"
    cmd = f"cd /root/nail_app; {rm_cmd} {extract}"

    proc = subprocess.Popen(
        [PLINK, "-hostkey", HOSTKEY, "-batch", "-pw", PW, "root@101.200.233.235", cmd],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    proc.stdin.write(b64.encode())
    proc.stdin.close()
    stdout_data = proc.stdout.read().decode("utf-8", errors="ignore")
    proc.wait()
    if "OK" in stdout_data.replace("\n", "").replace("\r", ""):
        print(f"  OK: {local_dir}/ -> {remote_dir}/")
        return True
    else:
        print(f"  FAIL: {local_dir}/ -> {remote_dir}/")
        print(f"  stdout: {stdout_data[:200]}")
        return False

def run_remote(cmd):
    """在远程执行命令"""
    proc = subprocess.Popen(
        [PLINK, "-hostkey", HOSTKEY, "-batch", "-pw", PW, "root@101.200.233.235", cmd],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    out, _ = proc.communicate()
    return out.decode("utf-8", errors="ignore")

print("=" * 50)
print("  NAIL TRYON - FULL DEPLOY")
print("=" * 50)

# Step 1: Code files
print("\n[1/4] Uploading code files...")
code_files = [
    ("nail_renderer.py", "/root/nail_app/nail_renderer.py"),
    ("nail_render_server.py", "/root/nail_app/nail_render_server.py"),
    ("nail_tryon_page.py", "/root/nail_app/nail_tryon_page.py"),
    ("nail_tryon_server.py", "/root/nail_app/nail_tryon_server.py"),
    ("nail_home_server.py", "/root/nail_app/nail_home_server.py"),
    ("nail_home_mobile.py", "/root/nail_app/nail_home_mobile.py"),
    ("nail_fav_page.py", "/root/nail_app/nail_fav_page.py"),
    ("nail_segmentor.py", "/root/nail_app/nail_segmentor.py"),
    ("deploy.sh", "/root/nail_app/deploy.sh"),
]
for local, remote in code_files:
    upload_file(os.path.join(PROJ, local), remote)

# Step 2: nail_cut3 directory
print("\n[2/4] Uploading nail_cut3 directory...")
cut3_dir = os.path.join(PROJ, "assets", "nail_cut3")
if os.path.isdir(cut3_dir):
    upload_tar_to_dir(cut3_dir, "/root/nail_app/assets/nail_cut3")

# Step 3: Verify files
print("\n[3/4] Verifying uploaded files...")
verify = run_remote("cd /root/nail_app && ls -la core/nail_renderer.py assets/nail_cut3/nail_points.csv 2>/dev/null && echo '---SIZE---' && wc -c core/nail_renderer.py assets/nail_cut3/nail_points.csv 2>/dev/null && echo '---CUT3---' && ls assets/nail_cut3/ 2>/dev/null | head -10")
print(verify.strip())

# Step 4: Restart
print("\n[4/4] Restarting services...")
restart = run_remote("cd /root/nail_app && bash deploy.sh")
for line in restart.split("\n"):
    line = line.strip()
    if line and not line.startswith("Last") and "Welcome" not in line and "Documentation" not in line:
        print(f"  {line}")

print("\n" + "=" * 50)
print("  DEPLOY COMPLETE!")
print("  Home: http://101.200.233.235:7860/")
print("=" * 50)