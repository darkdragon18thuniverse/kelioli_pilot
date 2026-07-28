#!/usr/bin/env bash
set -e

echo "🔄 Restarting Kelioli AI Pilot Core Backend Service..."

# 1. Stop server if already running
if systemctl is-active --quiet kelioli 2>/dev/null; then
    echo "🛑 Stopping currently running server..."
    sudo systemctl stop kelioli
fi

# Kill any stray gunicorn processes if present
pkill -f gunicorn 2>/dev/null || true

# 2. Reload systemd & start server
echo "▶️ Starting server service..."
sudo systemctl daemon-reload
sudo systemctl enable kelioli
sudo systemctl start kelioli

# 3. Show service status
echo "📊 Service Status:"
sudo systemctl status kelioli --no-pager -n 10 || true

echo "✅ Server started successfully!"
