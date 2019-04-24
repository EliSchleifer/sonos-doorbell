#!/bin/bash

pip install --upgrade pip
pip install SoCo
pip install mutagen
pip install netifaces

cd doorbell

python3 sonos-doorbell.py "Kitchen" --port 8888
