$plink = Join-Path "C:\Users\86180\Desktop\nail_project" "plink.exe"
$sshArgs = @("-hostkey","SHA256:0OkXtQ4+0rYdeya7dVvoidOrn8auFdnPN6Vi/W0wHqo","-batch","-pw","Yrj20020906","root@101.200.233.235")

Write-Host "====== CSV file check ======"
& $plink $sshArgs "ls -la /root/nail_app/nail_database/nail_product2.csv 2>/dev/null && wc -l /root/nail_app/nail_database/nail_product2.csv"
Write-Host ""
Write-Host "====== First 3 lines ======"
& $plink $sshArgs "head -3 /root/nail_app/nail_database/nail_product2.csv 2>/dev/null"
Write-Host ""
Write-Host "====== Raw images check ======"
& $plink $sshArgs "ls /root/nail_app/raw_images/ | head -5"
Write-Host ""
Write-Host "====== Tryon page check ======"
& $plink $sshArgs "curl -s 'http://localhost:7885/?from=1' | grep -c 'card-wrap\|product-card\|pi-v1\|fm'"
