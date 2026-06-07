param([switch]$Quick)

$hostkey  = "SHA256:0OkXtQ4+0rYdeya7dVvoidOrn8auFdnPN6Vi/W0wHqo"
$pw       = "Yrj20020906"
$user     = "root"
$hostname = "101.200.233.235"
$plink    = Join-Path $PSScriptRoot "plink.exe"
$projectRoot = Resolve-Path "$PSScriptRoot/.."
$remote   = "/root/nail_app"

$files = @(
    @{local="services\nail_home_server.py"; remote="services/nail_home_server.py"},
    @{local="services\nail_tryon_server.py"; remote="services\nail_tryon_server.py"},
    @{local="services\nail_fav_page.py"; remote="services\nail_fav_page.py"},
    @{local="core\nail_renderer.py"; remote="core/nail_renderer.py"},
    @{local="services\nail_render_server.py"; remote="services\nail_render_server.py"},
    @{local="core\nail_segmentor.py"; remote="core/nail_segmentor.py"},
    @{local="core\nail_quality_check.py"; remote="core/nail_quality_check.py"},
    @{local="core\ai_analyzer.py"; remote="core/ai_analyzer.py"},
    @{local="core\auto_optimizer.py"; remote="core/auto_optimizer.py"},
    @{local="core\llm_optimizer.py"; remote="core/llm_optimizer.py"},
    @{local="core\report_generator.py"; remote="core/report_generator.py"},
    @{local="core\startup_check.py"; remote="core/startup_check.py"},
    @{local="core\log_rotator.py"; remote="core/log_rotator.py"},
    @{local="services\report_scheduler.py"; remote="services/report_scheduler.py"},
    @{local="database\tryon_records_manager.py"; remote="database/tryon_records_manager.py"},
    @{local="database\config.json"; remote="database/config.json"},
    @{local="deploy\deploy.sh"; remote="deploy/deploy.sh"}
)

$csvFiles = @(
    @{local="nail_database\nail_product2.csv"; remote="nail_database/nail_product2.csv"}
)

Write-Host "========================================"
Write-Host "  NAIL AI - Deploy to Cloud Server"
Write-Host "========================================"
Write-Host ""

if (-not (Test-Path $plink)) {
    Write-Host "[ERROR] plink.exe not found!"
    exit 1
}

$sshArgs = @("-hostkey", $hostkey, "-batch", "-pw", $pw, "${user}@${hostname}")

# Step 1: Upload code files
Write-Host "[1/3] Uploading files..."

# Create remote directories first
& $plink $sshArgs "mkdir -p $remote/services $remote/core $remote/deploy $remote/nail_database $remote/database $remote/logs $remote/report 2>/dev/null" | Out-Null

function Upload-File {
    param($localPath, $remotePath)
    $remoteFull = "$remote/$remotePath"
    $bytes = [System.IO.File]::ReadAllBytes($localPath)
    $b64 = [Convert]::ToBase64String($bytes)
    $totalLen = $b64.Length
    $chunkSize = 3000

    # Write base64 to remote temp file in chunks
    $start = 0
    $chunkNum = 0
    while ($start -lt $totalLen) {
        $end = [Math]::Min($start + $chunkSize, $totalLen)
        $chunk = $b64.Substring($start, $end - $start)
        if ($chunkNum -eq 0) {
            & $plink $sshArgs "echo -n '$chunk' > /tmp/_deploy_b64" 2>$null
        } else {
            & $plink $sshArgs "echo -n '$chunk' >> /tmp/_deploy_b64" 2>$null
        }
        $start = $end
        $chunkNum++
    }

    # Decode to target path
    & $plink $sshArgs "base64 -d < /tmp/_deploy_b64 > $remoteFull 2>/dev/null; rm -f /tmp/_deploy_b64"
}

foreach ($f in $files) {
    $local = Join-Path $projectRoot $f.local
    if (-not (Test-Path $local)) {
        Write-Host "  [skip] $($f.local) (not found)"
        continue
    }
    Write-Host "  $($f.local) -> $($f.remote) ..."
    Upload-File $local $f.remote
    if ($LASTEXITCODE -eq 0) {
        Write-Host "    OK"
    } else {
        Write-Host "    FAILED!"
        exit 1
    }
}

# Step 1b: Upload database files
foreach ($f in $csvFiles) {
    $local = Join-Path $projectRoot $f.local
    if (-not (Test-Path $local)) {
        Write-Host "  [skip] $($f.local) (not found)"
        continue
    }
    Write-Host "  $($f.local) -> $($f.remote) ..."
    Upload-File $local $f.remote
    if ($LASTEXITCODE -eq 0) {
        Write-Host "    OK"
    } else {
        Write-Host "    FAILED!"
        exit 1
    }
}

# Step 2: Restart services
Write-Host "[2/3] Restarting services..."
& $plink $sshArgs "chmod +x $remote/deploy/deploy.sh; cd $remote; bash deploy/deploy.sh"
Write-Host "  Services restarted"

# Step 3: Verify
if ($Quick) {
    Write-Host "[3/3] Quick mode - skip verification"
} else {
    Write-Host "[3/3] Waiting for services (8 sec)..."
    Start-Sleep -Seconds 8

    $result = & $plink $sshArgs "ss -tlnp | grep -E '7860|7885|7886|7887'"
    $hasHome = $result -match "7860"
    $hasTryon = $result -match "7885"
    $hasFav = $result -match "7886"
    $hasRender = $result -match "7887"

    if ($hasHome -and $hasTryon -and $hasFav -and $hasRender) {
        Write-Host ""
        Write-Host "========================================"
        Write-Host "  DEPLOY SUCCESS!"
        Write-Host "  Home:   http://101.200.233.235:7860/"
        Write-Host "  Tryon:  http://101.200.233.235:7885/"
        Write-Host "  Fav:    http://101.200.233.235:7886/"
        Write-Host "  Render: http://101.200.233.235:7887/"
        Write-Host "  Open on your phone now!"
        Write-Host "========================================"
    } else {
        Write-Host ""
        Write-Host "  [WARN] Port verification failed"
        if (-not $hasHome) { Write-Host "  Port 7860 not listening" }
        if (-not $hasTryon) { Write-Host "  Port 7885 not listening" }
        if (-not $hasFav) { Write-Host "  Port 7886 not listening" }
        if (-not $hasRender) { Write-Host "  Port 7887 not listening" }
        Write-Host "  --- Home log ---"
        & $plink $sshArgs "tail -10 $remote/log_home.log"
        Write-Host "  --- Tryon log ---"
        & $plink $sshArgs "tail -10 $remote/log_tryon.log"
        Write-Host "  --- Fav log ---"
        & $plink $sshArgs "tail -10 $remote/log_fav.log"
        Write-Host "  --- Render log ---"
        & $plink $sshArgs "tail -10 $remote/log_render.log"
    }
}
