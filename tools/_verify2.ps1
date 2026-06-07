$plink = Join-Path "C:\Users\86180\Desktop\nail_project" "plink.exe"
$sshArgs = @("-hostkey","SHA256:0OkXtQ4+0rYdeya7dVvoidOrn8auFdnPN6Vi/W0wHqo","-batch","-pw","Yrj20020906","root@101.200.233.235")
Start-Sleep -Seconds 5
& $plink $sshArgs "ss -tlnp | grep -E '7860|7885'"
Write-Host "==="
& $plink $sshArgs "ps aux | grep python | grep -v grep | grep -v networkd | grep -v unattended | grep -v tuned"