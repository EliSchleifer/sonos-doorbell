#!/bin/bash

pip install SoCo
pip install mutagen

cd doorbell

python3 sonos-doorbell.py "Kitchen" --port 8888
