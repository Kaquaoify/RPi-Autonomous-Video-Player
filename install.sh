#!/bin/bash
set -euo pipefail

# install.sh (interactif)
# Installe l'application RPi-Autonomous-Video-Player et ses dépendances sur un RPi Ubuntu.

APP_NAME="RPi-Autonomous-Video-Player"
GIT_USER="kaquaoify"
REPO_URL="https://github.com/${GIT_USER}/${APP_NAME}.git"

USER_HOME="/home/$USER"
INSTALL_DIR="${USER_HOME}/${APP_NAME}"
VIDEO_DIR="${USER_HOME}/Videos/${APP_NAME}"
THUMB_DIR="${VIDEO_DIR}/thumbnails"
VENV_DIR="${INSTALL_DIR}/venv"
SERVICE_NAME="rpi-avp.service"
PYTHON_BIN="python3"
PIP_BIN="pip3"

confirm() {
    local prompt="$1"
    while true; do
        read -r -p "${prompt} (y/n) : " yn
        case "$yn" in
            [Yy]* ) break ;;
            [Nn]* ) echo "Annulation par l'utilisateur."; exit 1 ;;
            * ) echo "Merci de répondre par y (oui) ou n (non)." ;;
        esac
    done
}

echo "=== Installation de ${APP_NAME} ==="
echo "Utilisateur : $USER"
echo "Répertoire d'installation : ${INSTALL_DIR}"
echo

# 1) Mise à jour système
confirm "1) Mettre à jour le système (apt update && apt upgrade)"
sudo apt update && sudo apt upgrade -y
sudo apt autoremove -y

# 2) Installer paquets système essentiels
confirm "2) Installer paquets système essentiels (python3, python3-venv, python3-pip, vlc, ffmpeg, git, curl, unzip, rclone)"
sudo apt install -y python3 python3-venv python3-pip vlc ffmpeg git curl unzip rclone

# 3) Cloner ou mettre à jour le dépôt
if [ -d "${INSTALL_DIR}/.git" ]; then
    confirm "3) Mettre à jour le dépôt (${INSTALL_DIR}) depuis GitHub"
    cd "${INSTALL_DIR}"
    git fetch --all --prune
    git reset --hard origin/main || true
    git pull --rebase origin main || true
else
    confirm "3) Cloner le dépôt depuis ${REPO_URL} vers ${INSTALL_DIR}"
    git clone "${REPO_URL}" "${INSTALL_DIR}"
fi

# 4) Créer dossiers vidéos / miniatures
confirm "4) Créer le dossier vidéos et thumbnails (${VIDEO_DIR})"
mkdir -p "${VIDEO_DIR}"
mkdir -p "${THUMB_DIR}"
sudo chown -R "${USER}:${USER}" "${VIDEO_DIR}" "${THUMB_DIR}" "${INSTALL_DIR}"

# 5) Créer virtualenv Python
confirm "5) Créer l'environnement virtuel Python (venv) dans ${VENV_DIR}"
${PYTHON_BIN} -m venv "${VENV_DIR}"

# 6) Installer dépendances Python dans le venv
confirm "6) Installer les dépendances Python depuis ${INSTALL_DIR}/requirements.txt (si présent)"
source "${VENV_DIR}/bin/activate"
pip install --upgrade pip setuptools wheel

if [ -f "${INSTALL_DIR}/requirements.txt" ]; then
    pip install -r "${INSTALL_DIR}/requirements.txt"
else
    echo "Aucun requirements.txt trouvé dans ${INSTALL_DIR}. Installation minimale : flask, python-vlc, pillow"
    pip install flask python-vlc pillow
fi
deactivate

# 7) Vérification ffmpeg / vlc / python-vlc
echo "7) Vérifications rapides :"
if command -v ffmpeg >/dev/null 2>&1; then
    echo " - ffmpeg OK ($(ffmpeg -version | head -n1))"
else
    echo " - ffmpeg NON trouvé"
fi

if command -v vlc >/dev/null 2>&1; then
    echo " - vlc OK ($(vlc --version 2>/dev/null | head -n1 || true))"
else
    echo " - vlc NON trouvé"
fi

# 8) Créer service systemd (utilise python du venv)
confirm "8) Créer/mettre à jour le service systemd pour lancer l'application au démarrage"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}"

sudo tee "${SERVICE_FILE}" > /dev/null <<EOF
[Unit]
Description=RPi-Autonomous-Video-Player
After=network-online.target

[Service]
Type=simple
User=${USER}
WorkingDirectory=${INSTALL_DIR}
ExecStart=${VENV_DIR}/bin/python ${INSTALL_DIR}/app/web.py
Restart=always
RestartSec=3
Environment=PYTHONUNBUFFERED=1
Environment=VIRTUAL_ENV=${VENV_DIR}
Environment=PATH=${VENV_DIR}/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable "${SERVICE_NAME}"
sudo systemctl restart "${SERVICE_NAME}" || true

echo
echo "=== Installation terminée ==="
echo "Service systemd: ${SERVICE_NAME}"
echo "Pour voir le statut : sudo systemctl status ${SERVICE_NAME}"
echo "Logs (journal) : sudo journalctl -u ${SERVICE_NAME} -f"
echo "Interface accessible sur : http://$(hostname -I | awk '{print $1}'):5000"
