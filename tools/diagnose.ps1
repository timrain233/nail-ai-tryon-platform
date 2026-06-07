@"
========================================
  NAIL AI - Remote Diagnostic Script
  Run: plink root@101.200.233.235 "bash /root/nail_diag.sh"
========================================
"@

$plink = Join-Path "C:\Users\86180\Desktop\nail_project" "plink.exe"
$sshArgs = @("-hostkey","SHA256:0OkXtQ4+0rYdeya7dVvoidOrn8auFdnPN6Vi/W0wHqo","-batch","-pw","Yrj20020906","root@101.200.233.235")

# First, upload the diagnostic script
Write-Host "[1] Uploading diagnostic script..."
$diagScript = @'
#!/bin/bash
echo ""
echo "============================================"
echo "  NAIL AI - REMOTE DIAGNOSTIC"
echo "  $(date)"
echo "============================================"
echo ""

echo "===== 1. CSV FILE ====="
wc -l /root/nail_app/nail_database/nail_product2.csv 2>/dev/null || echo "MISSING!"
echo ""
echo "----- CSV header -----"
head -1 /root/nail_app/nail_database/nail_product2.csv 2>/dev/null || echo "No CSV"
echo ""
echo "----- CSV row count (excl header) -----"
if [ -f /root/nail_app/nail_database/nail_product2.csv ]; then
    PRODUCTS=$(tail -n +2 /root/nail_app/nail_database/nail_product2.csv | wc -l)
    echo "Products: $PRODUCTS"
    if [ "$PRODUCTS" -eq 0 ]; then
        echo "*** WARNING: No products in CSV! ***"
    fi
fi

echo ""
echo "===== 2. RAW IMAGES ====="
ls /root/nail_app/raw_images/ 2>/dev/null | wc -l
echo "images found"
ls /root/nail_app/raw_images/img_001* 2>/dev/null || echo "MISSING: img_001"

echo ""
echo "===== 3. PYTHON FILES ====="
for f in nail_home_mobile.py nail_tryon_page.py nail_fav_page.py deploy.sh; do
    if [ -f "/root/nail_app/$f" ]; then
        echo "  OK: $f ($(wc -c < /root/nail_app/$f) bytes)"
    else
        echo "  MISSING: $f"
    fi
done

echo ""
echo "===== 4. PORT STATUS ====="
ss -tlnp | grep -E '7860|7885|7886' || echo "  Ports not listening!"
echo ""

echo "===== 5. PYTHON PROCESSES ====="
ps aux | grep python | grep -v grep | grep -v networkd | grep -v unattended | grep -v tuned | awk '{print $2, $NF}'

echo ""
echo "===== 6. SERVICE LOGS (last 10 lines) ====="
echo "--- HOME ---"
tail -10 /root/nail_app/log_home.log 2>/dev/null || echo "No log"
echo "--- TRYON ---"
tail -10 /root/nail_app/log_tryon.log 2>/dev/null || echo "No log"
echo "--- FAV ---"
tail -10 /root/nail_app/log_fav.log 2>/dev/null || echo "No log"

echo ""
echo "===== 7. HTTP STATUS ====="
echo -n "Home: "
curl -s -o /dev/null -w '%{http_code}' http://localhost:7860/ 2>/dev/null || echo "FAIL"
echo ""
echo -n "Tryon: "
curl -s -o /dev/null -w '%{http_code}' http://localhost:7885/?from=1 2>/dev/null || echo "FAIL"
echo ""
echo -n "Fav: "
curl -s -o /dev/null -w '%{http_code}' http://localhost:7886/?device=test 2>/dev/null || echo "FAIL"
echo ""

echo ""
echo "===== 8. GRADIO CONFIG (check for empty card-wrap) ====="
curl -s http://localhost:7860/ 2>/dev/null | grep -oP 'card-wrap.*?/div>' | head -1 || echo "  No card-wrap found"
echo ""
echo "===== DIAGNOSTIC COMPLETE ====="
'@

# Write script to temp and upload
$diagPath = "C:\Users\86180\Desktop\nail_project\_nail_diag.sh"
Set-Content -Path $diagPath -Value $diagScript -Encoding ASCII

$b64 = [Convert]::ToBase64String([System.IO.File]::ReadAllBytes($diagPath))
$null = $b64 | & $plink $sshArgs "echo '$b64' | base64 -d > /root/nail_diag.sh && chmod +x /root/nail_diag.sh"
if ($LASTEXITCODE -eq 0) { Write-Host "  OK" } else { Write-Host "  FAILED"; exit 1 }

# Run the diagnostic
Write-Host ""
Write-Host "[2] Running diagnostic..."
& $plink $sshArgs "bash /root/nail_diag.sh"

# Cleanup
Remove-Item $diagPath -Force
