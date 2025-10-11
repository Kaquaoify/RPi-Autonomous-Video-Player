#!/usr/bin/env bash
set -euo pipefail

# --- Paramètres ----------------------------------------------------------------
# Dossier d'installation (par défaut: dossier courant)
INSTALL_DIR="${INSTALL_DIR:-$(pwd)}"
VENV_DIR="${VENV_DIR:-${INSTALL_DIR}/venv}"
SERVICE_NAME="${SERVICE_NAME:-rpi-avp.service}"
SERVICE_PATH="/etc/systemd/system/${SERVICE_NAME}"
PY_BIN="${PY_BIN:-python3}"

echo "[install] INSTALL_DIR=${INSTALL_DIR}"
echo "[install] VENV_DIR=${VENV_DIR}"
echo "[install] SERVICE=${SERVICE_PATH}"

# --- Vérifs de base ------------------------------------------------------------
if [ ! -f "${INSTALL_DIR}/requirements.txt" ]; then
  echo "[install] requirements.txt introuvable dans ${INSTALL_DIR}" >&2
  exit 1
fi

# --- Dépendances système utiles (optionnel, mais recommandé) -------------------
# Décommente si nécessaire:
# sudo apt-get update
# sudo apt-get install -y python3-venv python3-dev ffmpeg vlc rclone

# --- Création/MAJ de l'environnement virtuel ----------------------------------
if [ ! -d "${VENV_DIR}" ]; then
  echo "[install] Création du venv…"
  "${PY_BIN}" -m venv "${VENV_DIR}"
fi

echo "[install] MAJ pip + install requirements…"
"${VENV_DIR}/bin/pip" install --upgrade pip
"${VENV_DIR}/bin/pip" install -r "${INSTALL_DIR}/requirements.txt"

# --- Unité systemd -------------------------------------------------------------
# IMPORTANT: on lance l'app en mode MODULE (-m app.main) pour supporter les imports relatifs.
echo "[install] Écriture de l’unité systemd…"
sudo tee "${SERVICE_PATH}" >/dev/null <<UNIT
[Unit]
Description=RPi Autonomous Video Player
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
# Dossier racine du repo (celui qui contient le dossier app/)
WorkingDirectory=${INSTALL_DIR}

# Préparer la console FB pour un affichage propre (optionnel, sans échec bloquant)
ExecStartPre=/bin/chvt 1
ExecStartPre=/bin/sh -c '/usr/bin/con2fbmap 1 0 || /usr/sbin/con2fbmap 1 0 || true'

# Lancement en mode MODULE (corrige l’erreur des imports relatifs)
ExecStart=${VENV_DIR}/bin/python -m app.main

# Variables (tu peux ajuster le port si besoin)
Environment=FLASK_HOST=0.0.0.0
Environment=FLASK_PORT=5000
# Environment=FLASK_DEBUG=1

Restart=always
RestartSec=2
User=${USER}
# Si tu utilises des chemins dans /home/${USER}, assure-toi que ce user a accès aux fichiers/vidéos.

# Journalisation plus verbeuse au besoin:
# StandardOutput=journal
# StandardError=journal

[Install]
WantedBy=multi-user.target
UNIT

# --- Activation service --------------------------------------------------------
echo "[install] Reload daemon + enable + restart service…"
sudo systemctl daemon-reload
sudo systemctl enable "${SERVICE_NAME}"
sudo systemctl restart "${SERVICE_NAME}"

echo "[install] Statut du service:"
sudo systemctl status "${SERVICE_NAME}" --no-pager -l || true

echo
echo "[install] ✅ Installation terminée."
echo "[install] Test local: curl -s http://127.0.0.1:5000/health"
echo "[install] UI: http://<IP_DU_RPI>:5000/"
