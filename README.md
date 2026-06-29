# local_streaks
A local implementation of the ZStreak scanner page. Setup for this script is minimal and requires a ./cutouts/ directory to access image cutouts for scanning. Once a set of image cutouts are collected, place them into the cutouts directory in a subdirectory corresponding to the nightdate of the cutouts: ./cutouts/20260101/*

The scanning page is accessed by running server.py:

# Valid calls:  
python3 server.py 20251001 --indices 1-100  
python3 server.py 20251001 --indices 15-1500  
python3 server.py 20251001 --indices 1,5,42,100  
python3 server.py 20251001                      # all cutouts  

Runs on Python 3.9+  
Dependencies: argparse, os, re, csv, sys, webbrowser, threading, http, urllib
