"""
Deploy nail_tryon_server.py + nail_home_server.py + nail_fav_page.py + restart all
"""
import subprocess, base64, os

BASE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.abspath(os.path.join(BASE, ".."))
PLINK = os.path.join(BASE, "plink.exe")
HOSTKEY = "SHA256:0OkXtQ4+0rYdeya7dVvoidOrn8auFdnPN6Vi/W0wHqo"
PW = "Yrj20020906"

def upload_file(local, remote):
    with open(local, "rb") as f:
        data = f.read()
    b64 = base64.b64encode(data).decode()
    nm = os.path.basename(local)
    print(f"  Upload {nm} ({len(data)/1024:.1f} KB)...", end="")
    proc = subprocess.Popen(
        [PLINK, "-hostkey", HOSTKEY, "-batch", "-pw", PW, "root@101.200.233.235",
         "base64 -d > " + remote + "; echo OK"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    proc.stdin.write(b64.encode())
    proc.stdin.close()
    out = proc.stdout.read().decode("utf-8", errors="ignore")
    proc.wait()
    ok = "OK" in out
    print(f" {'OK' if ok else 'FAIL'}")
    return ok

files = [
    (os.path.join(PROJ, "services", "nail_tryon_server.py"), "/root/nail_app/services/nail_tryon_server.py"),
    (os.path.join(PROJ, "services", "nail_home_server.py"), "/root/nail_app/services/nail_home_server.py"),
    (os.path.join(PROJ, "services", "nail_fav_page.py"), "/root/nail_app/services/nail_fav_page.py"),
]
print("Uploading files...")
all_ok = all(upload_file(l, r) for l, r in files)
if not all_ok:
    print("Upload FAILED!")
    exit(1)

# Kill and restart all 3 services
print("\nRestarting services...")
cmd = """
kill -9 $(ps aux | grep 'nail_tryon_server' | grep -v grep | awk '{print $2}') 2>/dev/null
kill -9 $(ps aux | grep 'nail_home_server' | grep -v grep | awk '{print $2}') 2>/dev/null
kill -9 $(ps aux | grep 'nail_fav_page' | grep -v grep | awk '{print $2}') 2>/dev/null
sleep 3
cd /root/nail_app
nohup /root/nail_env/bin/python services/nail_home_server.py > log_home.log 2>&1 &
nohup /root/nail_env/bin/python services/nail_tryon_server.py > log_tryon.log 2>&1 &
nohup /root/nail_env/bin/python services/nail_fav_page.py > log_fav.log 2>&1 &
sleep 6
echo '---STATUS---'
for p in 7860 7885 7886 7887; do
  echo -n "Port $p: "
  if [ $p -eq 7887 ]; then
    curl -s -o /dev/null -w '%{http_code}' http://localhost:$p/health --max-time 3
  else
    curl -s -o /dev/null -w '%{http_code}' http://localhost:$p/ --max-time 3
  fi
  echo
done
"""
proc = subprocess.Popen(
    [PLINK, "-hostkey", HOSTKEY, "-batch", "-pw", PW, "root@101.200.233.235", cmd],
    stdout=subprocess.PIPE, stderr=subprocess.PIPE
)
out, _ = proc.communicate()
print(out.decode("utf-8", errors="ignore"))