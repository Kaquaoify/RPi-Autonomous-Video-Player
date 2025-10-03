#!/bin/bash
set -e

# ====================================================
# RPi-Autonomous-Video-Player - Install Script (Interactive)
# ====================================================
# Installation des dépendances et configuration du RPi
# pour exécuter le projet de manière autonome.
# L'utilisateur confirme chaque étape avec y/n.
# ====================================================

# Variables
USER_HOME=/home/$USER
REPO_URL="https://github.com/kaquaoify/RPi-Autonomous-Video-Player.git"
INSTALL_DIR="$USER_HOME/RPi-Autonomous-Video-Player"
VIDEO_DIR="$USER_HOME/Videos/RPi-Autonomous-Video-Player"
VENV_DIR="$INSTALL_DIR/venv"
SERVICE_NAME="rpi-avp"

# Fonction de confirmation y/n
confirm() {
    while true; do
        read -p "$1 [y/n]: " yn
        case $yn in
            [Yy]* ) break;;
            [Nn]* ) echo "Installation annulée."; exit;;
            * ) echo "y/n";;
        esac
    done
}

echo "Installation de RPi-Autonomous-Video-Player"

# -----------------------------
# 1. Mise à jour du système
# -----------------------------
confirm "Souhaitez-vous mettre à jour le système et les paquets ?"
echo "Mise à jour du système..."
sudo apt update && sudo apt upgrade -y
sudo apt autoremove -y
echo "Système mis à jour."

# -----------------------------
# 2. Installation des dépendances système
# -----------------------------
confirm "Souhaitez-vous installer les dépendances système ?"
echo "Installation des dépendances système..."
sudo apt install -y python3 python3-venv python3-pip vlc rclone git curl wget unzip
echo "Dépendances système installées."

# -----------------------------
# 3. Création des dossiers
# -----------------------------
confirm "Souhaitez-vous créer les dossiers nécessaires pour les vidéos ?"
echo "Création des dossiers..."
mkdir -p "$VIDEO_DIR"
echo "Dossiers créés : $VIDEO_DIR"

# -----------------------------
# 4. Clonage ou mise à jour du dépôt GitHub
# -----------------------------
confirm "Souhaitez-vous cloner ou mettre à jour le dépôt GitHub ?"
if [ -d "$INSTALL_DIR" ]; then
    echo "Le dépôt existe déjà, mise à jour..."
    cd "$INSTALL_DIR"
    git pull
else
    echo "Clonage du dépôt..."
    git clone "$REPO_URL" "$INSTALL_DIR"
fi
echo "Dépôt prêt dans $INSTALL_DIR"

# -----------------------------
# 5. Création du virtual environment
# -----------------------------
confirm "Souhaitez-vous créer le venv ?"
echo "Création du virtual environment..."
python3 -m venv "$VENV_DIR"

echo "Installation des dépendances Python dans le virtual environment..."
source "$VENV_DIR/bin/activate"
pip install --upgrade pip
pip install -r "$INSTALL_DIR/requirements.txt"
deactivate
echo "Virtual environment prêt."

# -----------------------------
# 6. Création du service systemd
# -----------------------------
confirm "Souhaitez-vous créer le service systemd pour lancer le projet automatiquement au démarrage ?"
echo "Création du service systemd..."
SERVICE_FILE="/etc/systemd/system/$SERVICE_NAME.service"

sudo bash -c "cat > $SERVICE_FILE" <<EOL
[Unit]
Description=RPi-Autonomous-Video-Player
After=network.target

[Service]
User=$USER
WorkingDirectory=$INSTALL_DIR
ExecStart=$VENV_DIR/bin/python $INSTALL_DIR/app/web.py
Restart=always

[Install]
WantedBy=multi-user.target
EOL

sudo systemctl daemon-reload
sudo systemctl enable $SERVICE_NAME
sudo systemctl start $SERVICE_NAME
echo "Service systemd '$SERVICE_NAME' actif."

echo "Installation terminée. Le projet est prêt et le service démarrera automatiquement au démarrage du système."
