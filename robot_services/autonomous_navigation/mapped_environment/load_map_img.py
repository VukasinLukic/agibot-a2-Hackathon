import matplotlib
import os
import os.glob
import subprocess
import sqlite3 as sql
import sys

# Exit code meanings:
# 0 --> Successful runtime, nothing went wrong.
# 1 --> Error finding map file system or map.

MAP_PATH = "/agibot/data/var/MapManagerModule/"

def load_dataset(MAP_PATH)
    # Make sure path exists before running this, so that it doesn't crash anything.
    try:
        os.path.isdir(MAP_PATH)
        print("Path exists! You may continue :D")
    except Exception as e:
        print(f"Failed to find the path on {MAP_PATH}.\nError listed as {e}")
        sys.exit(1) 
    
    actual_data_path = str(MAP_PATH + "map.db")
    load_base = sql.connect(actual_data_path)


    cursor = load_base.cursor() 
    load_base.close()
    




