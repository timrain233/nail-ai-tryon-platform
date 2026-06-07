#!/bin/bash
# Start both Gradio services
NAIL_DIR="/root/nail_app"
PYTHON="/root/nail_env/bin/python"

# Kill old processes
pkill -f "nail_home_mobile" 2>/dev/null
pkill -f "nail_tryon_page" 2>/dev/null
sleep 1

# Start home page (port 7884)
cd "$NAIL_DIR"
nohup $PYTHON nail_home_mobile.py > /root/nail_app/log_home.log 2>&1 &
echo "Home service started (PID $!) on port 7884"

sleep 3

# Start tryon page (port 7885)
nohup $PYTHON nail_tryon_page.py > /root/nail_app/log_tryon.log 2>&1 &
echo "Tryon service started (PID $!) on port 7885"

sleep 3

# Verify both services are running
echo ""
echo "=== Service Status ==="
ss -tlnp | grep -E "7884|7885" || echo "WAITING..."
sleep 2
ss -tlnp | grep -E "7884|7885"
echo ""
echo "Done! Access at:"
echo "  Home:  http://101.200.233.235:7884"
echo "  Tryon: http://101.200.233.235:7885"