import os
import subprocess
import sys
import socket
import vt # Use VirusTotal for an initial check for certainty.
import hashlib


def sha256_of(FILE_PATH):
    pass
# For the initial check, we will use VirusTotal, as it's quick to respond,
# and provides fairly accurate results. It is especially useful when it comes 
# to doing quick checks. If it comes back with most of them (Let's say about 90% sure) 
# saying its not a virus, then we can continue and allow it to be read/executed...
# Otherwise, we will call a more thorough check in to ensure there's no virus. 
def check_virus(FILE_PATH):
    client = vt.Client(API_KEY)
    

    pass
