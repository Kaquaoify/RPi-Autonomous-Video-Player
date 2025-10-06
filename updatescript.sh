#!/bin/bash
set -e
cd ~/RPi-Autonomous-Video-Player

# Forcer la mise à jour (ignorer les modifs locales)
git fetch --all --prune
git reset --hard origin/main
git clean -fd

source venv/bin/activate && pip install -r requirements.txt || true
sudo systemctl daemon-reload
sudo systemctl restart rpi-avp.service
sudo systemctl status rpi-avp.service --no-pager -l | sed -n '1,15p'
