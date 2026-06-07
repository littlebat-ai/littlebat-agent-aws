#!/usr/bin/env bash
# Bootstrap the Littlebat AI voice agent on Raspberry Pi 5 + Whisplay HAT
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── 1. Whisplay HAT drivers ───────────────────────────────────────────────────
echo ">>> Cloning Whisplay HAT repo..."
if [ ! -d "$SCRIPT_DIR/whisplay" ]; then
    git clone https://github.com/PiSugar/whisplay.git "$SCRIPT_DIR/whisplay" --depth 1
fi

echo ">>> Installing HAT drivers (requires sudo, will modify /boot and reboot)..."
cd "$SCRIPT_DIR/whisplay"
sudo bash install_driver.sh
cd "$SCRIPT_DIR"

# ── 2. System packages ────────────────────────────────────────────────────────
echo ">>> Installing system packages..."
sudo apt-get update -q
sudo apt-get install -y -q \
    python3-pip \
    python3-dev \
    alsa-utils \
    fonts-dejavu \
    awscli

# ── 3. Python packages ────────────────────────────────────────────────────────
echo ">>> Installing Python packages..."
pip install --break-system-packages \
    boto3 \
    Pillow \
    amazon-transcribe \
    spidev \
    gpiod

# ── 4. Config ─────────────────────────────────────────────────────────────────
if [ ! -f "$SCRIPT_DIR/config.env" ]; then
    cp "$SCRIPT_DIR/config.env.example" "$SCRIPT_DIR/config.env"
    echo ""
    echo ">>> Created config.env — edit it before starting the agent:"
    echo "    nano $SCRIPT_DIR/config.env"
fi

# ── 5. AWS credentials ────────────────────────────────────────────────────────
if [ ! -f "$HOME/.aws/credentials" ]; then
    echo ""
    echo ">>> AWS credentials not found. Run:"
    echo "    aws configure"
    echo "    (use the device access key: terraform output device_access_key_id"
    echo "     and terraform output -raw device_secret_access_key)"
fi

# ── 6. Systemd service ────────────────────────────────────────────────────────
SERVICE_FILE="$SCRIPT_DIR/whisplay-agent.service"
TMP_SERVICE=$(mktemp)

# Substitute actual paths and user into the service file
sed \
    -e "s|__SCRIPT_DIR__|$SCRIPT_DIR|g" \
    -e "s|__USER__|$(whoami)|g" \
    "$SERVICE_FILE" > "$TMP_SERVICE"

sudo cp "$TMP_SERVICE" /etc/systemd/system/whisplay-agent.service
rm "$TMP_SERVICE"
sudo systemctl daemon-reload

echo ""
echo ">>> Done. Next steps:"
echo "    1. sudo reboot  (to activate HAT drivers)"
echo "    2. Edit $SCRIPT_DIR/config.env"
echo "    3. aws configure  (if not done yet)"
echo "    4. sudo systemctl enable --now whisplay-agent"
