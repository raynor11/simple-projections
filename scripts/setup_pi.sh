#!/bin/bash
# Setup script for Raspberry Pi 4 running Raspberry Pi OS Lite 64-bit (Bookworm).
#
# Run from the repo root as the user that will own the services (not root):
#   ./scripts/setup_pi.sh

set -e

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
APP_USER="$(whoami)"

echo "Installing system dependencies..."
sudo apt-get update
# opencv/pygame/numpy come from apt rather than pip: Debian's pygame links
# the system SDL2, which is built with the kmsdrm backend we need to draw
# straight to the display without a desktop session.
sudo apt-get install -y \
    python3-pip \
    python3-venv \
    python3-dev \
    python3-opencv \
    python3-pygame \
    python3-numpy \
    python3-serial \
    v4l-utils \
    alsa-utils \
    gstreamer1.0-tools \
    gstreamer1.0-alsa \
    gstreamer1.0-plugins-good

echo "Creating Python virtual environment..."
python3 -m venv --system-site-packages .venv
source .venv/bin/activate

echo "Installing Python packages..."
pip install --upgrade pip setuptools wheel
pip install -r requirements-pi.txt

echo "Adding $APP_USER to hardware access groups..."
sudo usermod -aG video,render,input,audio,dialout "$APP_USER"

echo "Forcing 1080p60 HDMI output..."
# The projector upscales 1080p to 4K; rendering at 4K is too much for the Pi 4 GPU.
CMDLINE=/boot/firmware/cmdline.txt
if ! grep -q "video=HDMI-A-1:" "$CMDLINE"; then
    sudo sed -i '1 s/$/ video=HDMI-A-1:1920x1080@60/' "$CMDLINE"
fi

install_service() {
    local name="$1"
    sed -e "s|@USER@|$APP_USER|g" -e "s|@APP_DIR@|$APP_DIR|g" \
        "scripts/$name" | sudo tee "/etc/systemd/system/$name" > /dev/null
}

echo "Installing systemd services..."
install_service projection-mapper.service
sudo systemctl daemon-reload
sudo systemctl enable projection-mapper

echo "Setup complete! Reboot to apply group membership and the HDMI mode."
echo "To start the service: sudo systemctl start projection-mapper"
echo "To view logs: journalctl -u projection-mapper -f"
