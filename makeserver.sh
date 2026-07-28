#!/usr/bin/env bash
set -e

echo "🚀 Starting Kelioli AI Pilot Server Environment Setup..."

# 1. Install System Dependencies
echo "📦 Installing system packages (Python, FFmpeg, Nginx)..."
sudo apt-get update -y
sudo apt-get install -y python3-pip python3-venv ffmpeg libavcodec-dev libavformat-dev libswscale-dev nginx

# 2. Setup Python Virtual Environment
if [ ! -d ".venv" ]; then
    echo "🐍 Creating Python virtual environment (.venv)..."
    python3 -m venv .venv
fi

echo "📥 Installing/Updating Python dependencies..."
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt

# 3. Configure Systemd Service
echo "⚙️ Configuring systemd service (kelioli.service)..."
sudo cp deploy/kelioli.service /etc/systemd/system/kelioli.service
sudo sed -i "s/ubuntu/$USER/g" /etc/systemd/system/kelioli.service
sudo systemctl daemon-reload

# 4. Configure Nginx Reverse Proxy
echo "🌐 Configuring Nginx reverse proxy..."
if [ -f "deploy/nginx.conf" ]; then
    sudo cp deploy/nginx.conf /etc/nginx/sites-available/kelioli
    sudo sed -i 's/api.curigon.com/api.kelioli.curigon.com/g' /etc/nginx/sites-available/kelioli
    sudo ln -sf /etc/nginx/sites-available/kelioli /etc/nginx/sites-enabled/default
    sudo systemctl restart nginx
fi

# 5. Make runserver.sh executable
if [ -f "runserver.sh" ]; then
    chmod +x runserver.sh
fi

echo "✅ Server setup complete! You can now run './runserver.sh' to start/restart the server."
