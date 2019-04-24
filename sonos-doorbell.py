"""Sonos Doorbell

To use the script:

 * Make sure soco is installed
 * Drop this script into a folder that, besides python files, contains
nothing but music files
 * Choose which player to use and run the script at the command line as such:

sonos-doorbell.py "Living Room"

NOTE: The script has been changed from the earlier version, where the
settings were written directly into the file. They now have to be
given at the command line instead. But, it should only be necessary to
supply the zone name. The local machine IP should be autodetected.

"""

from __future__ import print_function, unicode_literals

import os
import os.path
import random
import sys
import socket
import time
import threading

from threading import Thread
from random import choice
from collections import namedtuple

from urllib.parse import quote, urlsplit, parse_qs
from http.server import SimpleHTTPRequestHandler, HTTPServer
from socketserver import TCPServer, ThreadingMixIn

from mutagen.mp3 import MP3

from soco import SoCo
from soco.snapshot import Snapshot
from soco.discovery import by_name, discover

music_files = []
doorbell_playing = False

AudioFile = namedtuple("AudioFile", "url length name key")

def is_doorbell_busy():
    global doorbell_playing
    return doorbell_playing

def on_doorbell(root_path, audio_file, volume, zone):
    global doorbell_playing
        
    http_path = root_path + "/" + audio_file.url
    print('on_doorbell {} {} {}'.format(audio_file.name, volume, zone))
    if is_doorbell_busy():
        print('Doorbell already playing...suppressing')
        return        
    doorbell_playing = True

    snap = Snapshot(zone)
    snap.snapshot()
            
    # Zone does not support snapshort restore properly for soundbar
    should_ring = zone.is_coordinator and not zone.is_playing_tv

    if should_ring:
        trans_state = zone.get_current_transport_info()
        if trans_state['current_transport_state'] == 'PLAYING':
            zone.pause()    
        zone.volume = volume

        print('Play doorbell on ', zone.player_name)        
        zone.play_uri(uri=http_path, title="Doorbell")
       
        time.sleep(audio_file.length)
        print('Restoring {}'.format(zone.player_name))
                
        if snap.is_playing_cloud_queue:
            print("Unlikely to resume playback. Cloud restore doesn't really work")
        snap.restore(fade=False)       
    else:
        print('Cannot play doorbell on the provided zone')
    doorbell_playing = False
 
class CustomRequestHandler(SimpleHTTPRequestHandler):   
    
    def send_text_response(self, code, body):
        self.send_response(code)
        self.send_header("Content-type", "text/html")
        self.end_headers()
        bytes = "<body>{} - {}</body>".format(code, body).encode('utf-8')
        self.wfile.write(bytes)
       
    def do_GET(self):
        if self.path.startswith('/doorbell_press'):    
            if is_doorbell_busy():
                self.send_text_response(429, "Doorbell already playing")
                return
                
            query = urlsplit(self.path).query            
            params = parse_qs(query)
            
            file_to_play = None
            requested_ringtone = "ringtone" in params and params["ringtone"]
            if requested_ringtone:                
                for file in music_files:                 
                    key = ''.join(params["ringtone"][0].split())                
                    if file.key == key:
                        file_to_play = file
                
            if not file_to_play:
                if requested_ringtone:
                    # A ringtone was requested but not found
                    msg = "Ringtone not found<br/><ul>"
                    for file in music_files:
                        msg += "<li>" + file.name + "</li>"
                    msg += "</ul>"
                    self.send_text_response(404, msg)
                    return
                else:
                    # Pick random ringtone
                    file_to_play = random.choice(music_files)        
            
            volume = 40
            requested_volume = "volume" in params and params["volume"]
            if requested_volume and requested_volume[0].isdigit():
                requested_volume = int(requested_volume[0])
                volume = max(min(100, requested_volume), 0)
            
            msg = "Doorbell received (request_id:{})".format(random.randint(1, 1000))
            self.send_text_response(200, msg)
            root_path = self.server.root_path
            on_doorbell(root_path, file_to_play, volume, self.server.zone)
        else:
            try:
                super().do_GET()      
            except BrokenPipeError:
                pass

class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    pass

def get_server(port, retry_bind, serve_path=None):
    Handler = CustomRequestHandler
    if serve_path:
        Handler.serve_path = serve_path
    while retry_bind >= 0:
        try:
            httpd = ThreadingHTTPServer(("", port), Handler)
            return httpd
        except socket.error as e:
            if e.errno == errno.EADDRINUSE:
                retry_bind -= 1
                time.sleep(3)
                print("Waiting 3 seconds for port to open...")
            else:
                raise

def get_zone(zone_name):
    import re
    devices = discover()
    cache_name = "{}_ip.txt".format(zone_name)
    if devices:
        for device in devices:
            if device.player_name == zone_name:
                # Save the well known IP of the device for future cache
                # When discover() fails need to fallback to this solution
                #try:                
                f= open(cache_name,"w+")
                f.write(device.ip_address)
                f.close()
                return device
    if not devices or len(devices) == 0:
        print("WARNING: No devices found through discover.")
    # Device not found, try be known IP
    try:        
        if os.path.isfile(cache_name) and os.access(cache_name, os.R_OK):
            f = open(cache_name,"r")
            line = f.readline()
            ip_address = re.search(r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}', line).group()
            f.close()
            print("Using cached IP:{} for zone:{}".format(ip_address, zone_name))
            device = SoCo(ip_address)            
            return device
        else:
            print("No cached record for zone")
    except:
        print("Exception reading cached record")
        return None
   
    

def load_music_files():
    """Add all music files from this folder and subfolders"""
    # Make a list of music files, right now it is done by collection all files
    # below the current folder whose extension starts with mp3/wav    
    print('Loading music files...')
    for path, dirs, files in os.walk('.'):
        for file_ in files:
            file_path = os.path.relpath(os.path.join(path, file_))
            url_path = os.path.join(*[quote(part) for part in os.path.split(file_path)])                
            ext = os.path.splitext(file_)[1].lower()
            name = os.path.splitext(file_)[0].lower()
            key = ''.join(name.split()) # unique key - no spaces
            audio_file = None
            if ext.startswith('.mp3'):
                audio = MP3(file_path)                                
                audio_file = AudioFile(url_path, audio.info.length, name, key)            
            if audio_file:
                music_files.append(audio_file)
                print('Found:', music_files[-1])

def detect_ip_address():
    """Return the local ip-address"""
    # Rather hackish way to get the local ip-address, recipy from
    # https://stackoverflow.com/a/166589
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.connect(("8.8.8.8", 80))
    ip_address = s.getsockname()[0]
    s.close()
    return ip_address

def parse_args():
    """Parse the command line arguments"""
    import argparse
    description = 'Play local files with Sonos by running a local web server'
    parser = argparse.ArgumentParser(description=description)

    parser.add_argument('zone', help='The name of the zone to play from')
    parser.add_argument('--port', default=8888, type=int,
                        help='The local machine port to run the webser on')
    parser.add_argument('--ip', default=detect_ip_address(),
                        help='The local IP address of this machine. By '
                        'default it will attempt to autodetect it.')

    return parser.parse_args()

def main():
    # Settings
    args = parse_args()
    print(" Will use the following settings:\n"
          " Zone: {args.zone}\n"
          " IP of this machine: {args.ip}\n"
          " Use port: {args.port}".format(args=args))

    # Get the zone
    zone = get_zone(args.zone)
    # Check if a zone by the given name was found
    if zone is None:
        print("No Sonos player named '{}'. Player names are {}"\
              .format(args.zone, discover()))
        sys.exit(1)

    # Check whether the zone is a coordinator (stand alone zone or master of a group)
    if not zone.is_coordinator:
        if not zone.group:
            print("This Zone does not belong to a valid group")
            sys.exit(2)
        if not zone.group.coordinator:
            print("The Zone '{}' has no coordinator. Cannot play music")
            sys.exit(2)
        print("The zone '{}' is not a group master, and therefore cannot "
              "play music. Please use '{}' in stead"\
              .format(args.zone, zone.group.coordinator.player_name))
        sys.exit(2)

    try:
        load_music_files()        
        http_server = get_server(args.port, 0, None)
        http_server.root_path = "http://{}:{}".format(args.ip, args.port)
        http_server.zone = zone
        http_server.serve_forever()
    except KeyboardInterrupt:
        print("Exiting")
        http_server.server_close()
        http_server.socket.close()

main()
