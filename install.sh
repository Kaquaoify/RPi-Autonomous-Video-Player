#!/bin/bash
set -euo pipefail

# install.sh (interactif)
# Installe l'application RPi-Autonomous-Video-Player et ses dÃ©pendances sur un RPi Ubuntu.

APP_NAME="RPi-Autonomous-Video-Player"
GIT_USER="kaquaoify"
REPO_URL="https://github.com/${GIT_USER}/${APP_NAME}.git"

USER_NAME="${SUDO_USER:-$(id -un)}"
USER_HOME="$(getent passwd "$USER_NAME" | cut -d: -f6)"
[ -z "$USER_HOME" ] && USER_HOME="/home/$USER_NAME"
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
            * ) echo "Merci de rÃ©pondre par y (oui) ou n (non)." ;;
        esac
    done
}

echo "=== Installation de ${APP_NAME} ==="
echo "Utilisateur cible : $USER_NAME"
echo "RÃ©pertoire d'installation : ${INSTALL_DIR}"
echo

# 1) Mise Ã  jour systÃ¨me
confirm "1) Mettre Ã  jour le systÃ¨me (apt update && apt upgrade)"
sudo apt update && sudo apt upgrade -y
sudo apt autoremove -y

# 2) Installer paquets systÃ¨me essentiels (+ fbset pour con2fbmap)
confirm "2) Installer paquets systÃ¨me essentiels (python3, venv, pip, vlc, vlc-plugin-base, ffmpeg, git, curl, unzip, rclone, fbset)"
sudo apt install -y python3 python3-venv python3-pip vlc vlc-plugin-base ffmpeg git curl unzip rclone fbset

# 3) Cloner ou mettre Ã  jour le dÃ©pÃ´t
if [ -d "${INSTALL_DIR}/.git" ]; then
    confirm "3) Mettre Ã  jour le dÃ©pÃ´t (${INSTALL_DIR}) depuis GitHub"
    cd "${INSTALL_DIR}"
    git fetch --all --prune
    git reset --hard origin/main || true
    git pull --rebase origin main || true
else
    confirm "3) Cloner le dÃ©pÃ´t depuis ${REPO_URL} vers ${INSTALL_DIR}"
    git clone "${REPO_URL}" "${INSTALL_DIR}"
fi

# 4) CrÃ©er dossiers vidÃ©os / miniatures
confirm "4) CrÃ©er le dossier vidÃ©os et thumbnails (${VIDEO_DIR})"
mkdir -p "${VIDEO_DIR}"
mkdir -p "${THUMB_DIR}"
sudo chown -R "${USER_NAME}:${USER_NAME}" "${VIDEO_DIR}" "${THUMB_DIR}" "${INSTALL_DIR}"

# 5) CrÃ©er virtualenv Python
confirm "5) CrÃ©er l'environnement virtuel Python (venv) dans ${VENV_DIR}"
${PYTHON_BIN} -m venv "${VENV_DIR}"

# 6) Installer dÃ©pendances Python dans le venv
confirm "6) Installer les dÃ©pendances Python depuis ${INSTALL_DIR}/requirements.txt (si prÃ©sent)"
source "${VENV_DIR}/bin/activate"
pip install --upgrade pip setuptools wheel
if [ -f "${INSTALL_DIR}/requirements.txt" ]; then
    pip install -r "${INSTALL_DIR}/requirements.txt"
else
    echo "Aucun requirements.txt trouvÃ© dans ${INSTALL_DIR}. Installation minimale : flask, python-vlc, pillow"
    pip install flask python-vlc pillow
fi
deactivate

# 7) VÃ©rification ffmpeg / vlc / python-vlc
echo "7) VÃ©rifications rapides :"
if command -v ffmpeg >/dev/null 2>&1; then
    echo " - ffmpeg OK ($(ffmpeg -version | head -n1))"
else
    echo " - ffmpeg NON trouvÃ©"
fi
if command -v vlc >/dev/null 2>&1; then
    echo " - vlc OK ($(vlc --version 2>/dev/null | head -n1 || true))"
else
    echo " - vlc NON trouvÃ©"
fi

# 8) Ajouter l'utilisateur aux groupes nÃ©cessaires pour l'accÃ¨s vidÃ©o direct
confirm "8) Ajouter l'utilisateur '' aux groupes video, render, input, audio (accÃ¨s /dev/fb0 et DRM)"
sudo usermod -aG video,render,input,audio "${USER_NAME}"
echo "   -> Les nouveaux groupes seront effectifs pour le service systemd dÃ¨s son prochain dÃ©marrage."

# 9) DÃ©sactiver la console login sur TTY1 pour libÃ©rer l'affichage
confirm "9) DÃ©sactiver getty@tty1 (libÃ¨re la console HDMI pour la sortie vidÃ©o)"
sudo systemctl disable --now getty@tty1.service || true

# 10) CrÃ©er/mettre Ã  jour le service systemd (utilise python du venv) + TTY HDMI
confirm "10) CrÃ©er/mettre Ã  jour le service systemd pour lancer l'application au dÃ©marrage (prise TTY1 + framebuffer)"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}"

sudo tee "${SERVICE_FILE}" > /dev/null <<EOF
[Unit]
Description=RPi Autonomous Video Player
After=network-online.target sound.target
Wants=network-online.target

[Service]
Type=simple
User=${USER_NAME}
WorkingDirectory=${INSTALL_DIR}

# Environnement (venv + runtime)
Environment=PYTHONUNBUFFERED=1
Environment=VIRTUAL_ENV=${VENV_DIR}
Environment=PATH=${VENV_DIR}/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
Environment=HOME=${USER_HOME}

# Fournit un /run dÃ©diÃ© et propre pour l'app
RuntimeDirectory=rpi-avp
RuntimeDirectoryMode=0700
Environment=XDG_RUNTIME_DIR=/run/rpi-avp

# Attacher au TTY1 (sortie HDMI via framebuffer)
TTYPath=/dev/tty1
StandardInput=tty
StandardOutput=journal+console
StandardError=journal+console
TTYReset=yes
TTYVHangup=yes
TTYVTDisallocate=yes
# Basculer sur le TTY1 et mapper la console vers fb0 (fallback si binaire ailleurs)
ExecStartPre=/bin/chvt 1
ExecStartPre=/bin/sh -c '/usr/bin/con2fbmap 1 0 || /usr/sbin/con2fbmap 1 0 || true'

# Lancement de l'app (libVLC choisira --vout=fb cÃ´tÃ© headless via le code Python)
ExecStart=${VENV_DIR}/bin/python ${INSTALL_DIR}/run.py

Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable "${SERVICE_NAME}"
sudo systemctl restart "${SERVICE_NAME}" || true

echo
echo "=== Installation terminÃ©e ==="
echo "Service systemd: ${SERVICE_NAME}"
echo "Pour voir le statut : sudo systemctl status ${SERVICE_NAME}"
echo "Logs (journal) : sudo journalctl -u ${SERVICE_NAME} -f"
echo "Interface accessible sur : http://$(hostname -I | awk '{print $1}'):5000"
echo
echo "Note:"
echo "- Les droits groupes (video/render/input/audio) sont pris en compte par le service lors du (re)dÃ©marrage."
echo "- Si jamais l'affichage HDMI ne sort pas tout de suite, refaites simplement :"
echo "    sudo systemctl restart ${SERVICE_NAME}"
