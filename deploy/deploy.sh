#!/bin/bash
# deploy.sh - 远程重启脚本，由 deploy.ps1 上传后执行
cd /root/nail_app

echo "=== 停止旧进程 ==="
pkill -9 -f "nail_home" 2>/dev/null
pkill -9 -f "nail_render" 2>/dev/null
pkill -9 -f "nail_fav" 2>/dev/null
pkill -9 -f "nail_tryon" 2>/dev/null
pkill -9 -f "report_scheduler" 2>/dev/null
sleep 2

echo "=== 启动服务 ==="

nohup /root/nail_env/bin/python services/nail_home_server.py > log_home.log 2>&1 &
PID1=$!
echo "Home (7860) PID: $PID1"

nohup /root/nail_env/bin/python services/nail_tryon_server.py > log_tryon.log 2>&1 &
PID2=$!
echo "Tryon (7885) PID: $PID2"

nohup /root/nail_env/bin/python services/nail_fav_page.py > log_fav.log 2>&1 &
PID3=$!
echo "Fav (7886) PID: $PID3"

nohup /root/nail_env/bin/python services/nail_render_server.py > log_render.log 2>&1 &
PID4=$!
echo "Render (7887) PID: $PID4"

nohup /root/nail_env/bin/python services/report_scheduler.py > log_scheduler.log 2>&1 &
PID5=$!
echo "Scheduler PID: $PID5"

# 等待报表首次运行
sleep 3

echo "=== 健康检查 ==="
for port in 7860 7885 7886 7887; do
  code=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:$port/ --max-time 3 2>/dev/null)
  if [ "$code" = "200" ] || [ "$code" = "404" ]; then
    echo "  port $port: $code OK"
  else
    echo "  port $port: $code FAILED"
  fi
done

echo "=== 部署完成 ==="
