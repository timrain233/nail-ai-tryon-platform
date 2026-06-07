$plink = Join-Path "C:\Users\86180\Desktop\nail_project" "plink.exe"
$sshArgs = @("-hostkey", "SHA256:0OkXtQ4+0rYdeya7dVvoidOrn8auFdnPN6Vi/W0wHqo", "-batch", "-pw", "Yrj20020906", "root@101.200.233.235")
& $plink $sshArgs "curl -s http://localhost:7860/ | head -200"