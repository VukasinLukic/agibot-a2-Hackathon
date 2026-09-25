import sys
import argparse
import os
import subprocess


def get_time():
    return subprocess.run("date")

def change_time_zone(new_zone):
    new_zone_corr = new_zone[:1].upper() + new_zone[1:]
    
    try:
        output_zone = subprocess.run("timedatectl", "|", "grep", new_zone_corr)
    except Exception as e:
        print(f"ERROR due to {e}")
        print(f"CANNOT FIND THIS CITY PLEASE CHECK SPELLING OR CITY'S EXISTENCE!")
    
    output_zone = 