$plink = Join-Path "C:\Users\86180\Desktop\nail_project" "plink.exe"
$sshArgs = @("-hostkey","SHA256:0OkXtQ4+0rYdeya7dVvoidOrn8auFdnPN6Vi/W0wHqo","-batch","-pw","Yrj20020906","root@101.200.233.235")

Write-Host "====== 1. Check if CSS has fatal rules ======"
& $plink $sshArgs "grep -n 'display:none\|empty\|::before\|::after\|progress-bar\|loading' /root/nail_app/nail_home_mobile.py | head -30"

Write-Host ""
Write-Host "====== 2. Check if the HTML contains card-wrap/content ======"
& $plink $sshArgs "curl -s http://localhost:7860/ | grep -c 'card-wrap\|product-card\|tag-row'"

Write-Host ""
Write-Host "====== 3. Check Python startup errors ======"
& $plink $sshArgs "tail -20 /root/nail_app/log_home.log"

Write-Host ""
Write-Host "====== 4. Check full HTML head section ======"
& $plink $sshArgs "curl -s http://localhost:7860/ | head -80"