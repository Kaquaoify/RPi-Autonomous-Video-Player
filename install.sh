#!/bin/bash

echo "Installation en cours"

# Met à jour le système
sudo apt update && sudo apt upgrade -y

# Installe Python3 et pip
sudo apt install -y python3 python3-pip vlc rclone

# Installe les dépendances Python
pip3 install -r requirements.txt

# Crée un dossier pour les vidéos
mkdir -p ~/Videos/raspi-player

echo "✅ Installation terminée !"
