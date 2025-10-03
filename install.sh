#!/bin/bash
set -e

# ====================================================
# RPi-Autonomous-Video-Player - Install Script
# ====================================================
# Ce script installe toutes les dépendances et configure
# le Raspberry Pi pour exécuter le projet au démarrage.
# ====================================================

# Variables
USER_HOME=/home/$USER
REPO_URL="https://github.com/kaquaoify/RPi-Autonomous-Video-Player.git"
INSTALL_DIR="$USER_HOME/RPi-Autonomous-Video-Player"
VIDEO_DIR="$USER_HOME/Videos/RPi-Autonomous-Video-Player"
SERVICE_NAME="rpi-avp"

echo "Installation de RPi-Autonomous-Video-Player"

# -----------------------------
# 1. Mise à jour du système
# -----------------------------
echo "Mise à jour du système"
sudo apt update && sudo apt upgrade -y
sudo apt autoremove -y

# -----------------------------
# 2. Installation des dépendances système
# -----------------------------
echo "Installation des dépendances système"
sudo apt install -y python3 python3-pip python3-venv vlc rclone git curl wget unzip

# -----------------------------
# 3. Création des dossiers
# -----------------------------
echo "Création des dossiers nécessaires"
mkdir -p "$VIDEO_DIR"

# -----------------------------
# 4. Clonage du projet depuis GitHub
# -----------------------------
if [ -d "$INSTALL_DIR" ]; then
    echo "Le dépôt existe déjà, mise à jour"
    cd "$INSTALL_DIR"
    git pull
else
    echo "Clonage du dépôt"
    git clone "$REPO_URL" "$INSTALL_DIR"
fi

# -----------------------------
# 5. Installation des dépendances Python
# -----------------------------
echo "Installation des dépendances Python"
pip3 install --upgrade pip
pip3 install -r "$INSTALL_DIR/requirements.txt"

# -----------------------------
# 6. Création du service systemd pour démarrage automatique
# -----------------------------
echo "Configuration du service systemd"

SERVICE_FILE="/etc/systemd/system/$SERVICE_NAME.service"

sudo bash -c "cat > $SERVICE_FILE" <<EOL
[Unit]
Description=RPi-Autonomous-Video-Player
After=network.target

[Service]
User=$USER
WorkingDirectory=$INSTALL_DIR
ExecStart=/usr/bin/python3 $INSTALL_DIR/app/web.py
Restart=always

[Install]
WantedBy=multi-user.target
EOL

sudo systemctl daemon-reload
sudo systemctl enable $SERVICE_NAME
sudo systemctl start $SERVICE_NAME

echo "Installation terminée. Le service '$SERVICE_NAME' est en cours d'exécution et démarrera automatiquement au boot."
