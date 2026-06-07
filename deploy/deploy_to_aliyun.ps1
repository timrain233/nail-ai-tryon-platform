$hostkey = "SHA256:0OkXtQ4+0rYdeya7dVvoidOrn8auFdnPN6Vi/W0wHqo"
$password = "Yrj20020906"
$remoteUser = "root"
$remoteAddr = "101.200.233.235"
$plink = ".\plink.exe"

Write-Host "============================================"
Write-Host "  NAIL TRYON - CLOUD DEPLOY"
Write-Host "============================================"

# Step 1: Upload Python files
Write-Host "[1/3] Uploading code files..."
$projectRoot = Resolve-Path "$PSScriptRoot/.."
$codeFiles = @("core/nail_renderer.py","services/nail_render_server.py","services/nail_tryon_server.py","services/nail_fav_page.py","core/nail_segmentor.py","deploy/deploy.sh")
foreach ($file in $codeFiles) {
    $localPath = Join-Path $projectRoot $file
    if (Test-Path $localPath) {
        Write-Host "  -> $file" -NoNewline
        $b64 = [Convert]::ToBase64String([System.IO.File]::ReadAllBytes($localPath))
        $remoteFile = "/root/nail_app/$file" -replace '\\', '/'
        $b64 | & $plink -hostkey $hostkey -batch -pw $password "${remoteUser}@${remoteAddr}" "base64 -d > '$remoteFile'; echo ' OK'"
    } else {
        Write-Host "  SKIP $file (not found)"
    }
}

# Step 2: Upload nail_cut3 via tar
Write-Host "[2/3] Uploading nail_cut3/ via tar..."
$tarFile = "$env:TEMP/nail_cut3_upload.tar.gz"
tar -czf $tarFile -C $PSScriptRoot/.. assets/nail_cut3 2>&1 | Out-Null
if (Test-Path $tarFile) {
    Write-Host "  Tar size: $((Get-Item $tarFile).Length / 1KB) KB" -NoNewline
    $b64 = [Convert]::ToBase64String([System.IO.File]::ReadAllBytes($tarFile))
    $b64 | & $plink -hostkey $hostkey -batch -pw $password "${remoteUser}@${remoteAddr}" "cd /root/nail_app; rm -rf assets/nail_cut3_old 2>/dev/null; mv assets/nail_cut3 assets/nail_cut3_old 2>/dev/null; base64 -d | tar -xzf -; echo ' nail_cut3 OK'"
    Remove-Item $tarFile -Force
} else {
    Write-Host "  FAILED to create tar"
}

# Step 3: Restart services
Write-Host "[3/3] Restarting services..."
& $plink -hostkey $hostkey -batch -pw $password "${remoteUser}@${remoteAddr}" "cd /root/nail_app; bash deploy.sh"

Write-Host "============================================"
Write-Host "  DEPLOY COMPLETE!"
Write-Host "  Home: http://101.200.233.235:7860/"
Write-Host "============================================"