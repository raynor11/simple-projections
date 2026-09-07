#!/bin/bash
# Setup script for Raspberry Pi

set -e

echo "Installing system dependencies..."
sudo apt-get update
sudo apt-get install -y \
    python3-pip \
    python3-dev \
    libgl1-mesa-glx \
    libopencv-dev \
    python3-opencv \
    libmpv-dev \
    libsdl2-dev

echo "Creating Python virtual environment..."
python3 -m venv venv
source venv/bin/activate

echo "Installing Python packages..."
pip install --upgrade pip setuptools wheel
pip install -r requirements.txt

echo "Setting up rotation configuration..."
# This depends on display server; for now just document the step
echo "Note: Configure display rotation in /boot/config.txt or via xrandr/wlr-randr as needed"

echo "Installing systemd service..."
sudo cp scripts/projection-mapper.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable projection-mapper

echo "Setup complete!"
echo "To start the service: sudo systemctl start projection-mapper"
echo "To view logs: journalctl -u projection-mapper -f"
