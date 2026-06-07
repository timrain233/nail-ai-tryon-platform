$src = "C:\Users\86180\Desktop\nail_project\checkpoints\nail_segment.onnx"
$dst = "C:\Users\86180\Desktop\nail_project\_tmp_onnx.b64"

Write-Host "Encoding to base64..."
[Convert]::ToBase64String([System.IO.File]::ReadAllBytes($src)) | Out-File -FilePath $dst -NoNewline -Encoding ascii

$f = Get-Item $dst
Write-Host "Base64 file: $($f.Length) bytes"
