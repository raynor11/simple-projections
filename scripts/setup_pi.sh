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
# Kernel headers first: v4l2loopback-dkms (below) builds against them.
sudo apt-get install -y linux-headers-rpi-v8 || sudo apt-get install -y raspberrypi-kernel-headers || true
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
    gstreamer1.0-plugins-good \
    gstreamer1.0-plugins-bad \
    gstreamer1.0-libav \
    avahi-daemon \
    uxplay \
    v4l2loopback-dkms \
    nut

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

echo "Configuring the AirPlay video loopback device and HDMI audio..."
sudo cp scripts/system/v4l2loopback.conf /etc/modprobe.d/v4l2loopback.conf
echo v4l2loopback | sudo tee /etc/modules-load.d/v4l2loopback.conf > /dev/null
sudo modprobe v4l2loopback || echo "v4l2loopback will load after reboot"
sudo cp scripts/system/asound.conf /etc/asound.conf

echo "Configuring NUT for the APC UPS..."
UPSMON_PASSWORD="$(sudo sed -n 's/^ *password = //p' /etc/nut/upsd.users 2>/dev/null | head -1)"
UPSMON_PASSWORD="${UPSMON_PASSWORD:-$(head -c 18 /dev/urandom | base64 | tr -dc 'A-Za-z0-9')}"
sudo cp scripts/nut/ups.conf scripts/nut/nut.conf /etc/nut/
for f in upsd.users upsmon.conf; do
    sed "s|@UPSMON_PASSWORD@|$UPSMON_PASSWORD|g" "scripts/nut/$f" | sudo tee "/etc/nut/$f" > /dev/null
done
sudo chown root:nut /etc/nut/*.conf /etc/nut/upsd.users
sudo chmod 640 /etc/nut/*.conf /etc/nut/upsd.users
sudo systemctl enable nut-server nut-monitor
sudo systemctl restart nut-server nut-monitor || echo "NUT will start once the UPS is connected over USB"

install_service() {
    local name="$1"
    sed -e "s|@USER@|$APP_USER|g" -e "s|@APP_DIR@|$APP_DIR|g" \
        "scripts/$name" | sudo tee "/etc/systemd/system/$name" > /dev/null
}

echo "Installing systemd services..."
install_service projection-mapper.service
install_service uxplay.service
install_service projector-control.service
sudo systemctl daemon-reload
sudo systemctl enable projection-mapper uxplay projector-control

echo "Setup complete! Reboot to apply group membership and the HDMI mode."
echo "To start the service: sudo systemctl start projection-mapper"
echo "To view logs: journalctl -u projection-mapper -f"
