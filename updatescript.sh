cat > ~/RPi-Autonomous-Video-Player/update.sh <<'SH'
set -e
cd ~/RPi-Autonomous-Video-Player
git fetch --all --prune
git pull --rebase origin main
source venv/bin/activate && pip install -r requirements.txt || true
sudo systemctl daemon-reload
sudo systemctl restart rpi-avp.service
sudo systemctl status rpi-avp.service --no-pager -l | sed -n '1,15p'
SH
chmod +x ~/RPi-Autonomous-Video-Player/update.sh