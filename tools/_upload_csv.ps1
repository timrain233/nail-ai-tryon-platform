$plink = Join-Path "C:\Users\86180\Desktop\nail_project" "plink.exe"
$hostkey = "SHA256:0OkXtQ4+0rYdeya7dVvoidOrn8auFdnPN6Vi/W0wHqo"
$sshArgs = @("-hostkey",$hostkey,"-batch","-pw","Yrj20020906","root@101.200.233.235")

Write-Host "[Upload] nail_product2.csv ..."
# Ensure directory exists
& $plink $sshArgs "mkdir -p /root/nail_app/nail_database"
# Upload
$local = "C:\Users\86180\Desktop\nail_project\_tmp_csv.csv"
$b64 = [Convert]::ToBase64String([System.IO.File]::ReadAllBytes($local))
$null = $b64 | & $plink $sshArgs "echo '$b64' | base64 -d > /root/nail_app/nail_database/nail_product2.csv"
if ($LASTEXITCODE -eq 0) { Write-Host "  OK" } else { Write-Host "  FAILED" }

# Verify
& $plink $sshArgs "wc -c /root/nail_app/nail_database/nail_product2.csv && head -2 /root/nail_app/nail_database/nail_product2.csv"