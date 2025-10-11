#!/usr/bin/env bash
set -euo pipefail

# =========================
#  Config par défaut (editables via variables d'env)
# =========================
REPO_URL="${REPO_URL:-https://github.com/Kaquaoify/RPi-Autonomous-Video-Player.git}"
BRANCH="${BRANCH:-main}"
APP_DIR="${APP_DIR:-$HOME/RPi-Autonomous-Video-Player}"
VENV_DIR="${VENV_DIR:-$APP_DIR/venv}"
SERVICE_NAME="${SERVICE_NAME:-rpi-avp.service}"
SERVICE_PATH="/etc/systemd/system/${SERVICE_NAME}"
PY_BIN="${PY_BIN:-python3}"

echo "[install] REPO_URL=${REPO_URL}"
echo "[install] BRANCH=${BRANCH}"
echo "[install] APP_DIR=${APP_DIR}"
echo "[install] VENV_DIR=${VENV_DIR}"
echo "[install] SERVICE=${SERVICE_PATH}"
echo

# =========================
#  Dépendances système
# =========================
echo "[install] Installation des paquets système requis…"
sudo apt-get update -y
sudo apt-get install -y git ${PY_BIN}-venv ${PY_BIN}-dev ffmpeg vlc rclone

# =========================
#  Clonage / Mise à jour du dépôt
# =========================
if [ -d "${APP_DIR}/.git" ]; then
  echo "[install] Repo déjà présent → mise à jour…"
  git -C "${APP_DIR}" fetch --all
  git -C "${APP_DIR}" checkout "${BRANCH}"
  git -C "${APP_DIR}" pull --ff-only origin "${BRANCH}"
else
  echo "[install] Clonage du dépôt…"
  git clone --branch "${BRANCH}" --depth 1 "${REPO_URL}" "${APP_DIR}"
fi

# =========================
#  Environnement virtuel + requirements
# =========================
if [ ! -d "${VENV_DIR}" ]; then
  echo "[install] Création du venv…"
  "${PY_BIN}" -m venv "${VENV_DIR}"
fi

echo "[install] MAJ pip + install requirements…"
"${VENV_DIR}/bin/pip" install --upgrade pip
if [ ! -f "${APP_DIR}/requirements.txt" ]; then
  echo "[install] ERREUR: requirements.txt introuvable dans ${APP_DIR}" >&2
  exit 1
fi
"${VENV_DIR}/bin/pip" install -r "${APP_DIR}/requirements.txt"

# =========================
#  Unité systemd (mode module)
# =========================
echo "[install] Écriture de l’unité systemd…"
CURRENT_USER="$(id -un)"
sudo tee "${SERVICE_PATH}" >/dev/null <<UNIT
[Unit]
Description=RPi Autonomous Video Player
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
# Dossier racine du repo (contient app/)
WorkingDirectory=${APP_DIR}

# Préparer la console FB (optionnel, non bloquant)
ExecStartPre=/bin/chvt 1
ExecStartPre=/bin/sh -c '/usr/bin/con2fbmap 1 0 || /usr/sbin/con2fbmap 1 0 || true'

# Lancement en MODE MODULE (permet les imports relatifs: from . import …)
ExecStart=${VENV_DIR}/bin/python -m app.main

# Expose les variables si besoin
Environment=FLASK_HOST=0.0.0.0
Environment=FLASK_PORT=5000
# Environment=FLASK_DEBUG=1

Restart=always
RestartSec=2
User=${CURRENT_USER}

[Install]
WantedBy=multi-user.target
UNIT

# =========================
#  Activer & démarrer
# =========================
echo "[install] Activation + (re)démarrage du service…"
sudo systemctl daemon-reload
sudo systemctl enable "${SERVICE_NAME}"
sudo systemctl restart "${SERVICE_NAME}"

echo
echo "[install] Statut:"
sudo systemctl status "${SERVICE_NAME}" --no-pager -l || true

echo
IP_ADDRS="$(hostname -I || true)"
echo "[install] ✅ Terminé."
echo "[install] Healthcheck local :  curl -s http://127.0.0.1:5000/health"
echo "[install] UI depuis le LAN  :  http://${IP_ADDRS%% *}:5000/"
