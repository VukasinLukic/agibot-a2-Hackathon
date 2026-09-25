# Get the robot's location based on maps seen before, and figure out the robot's
# exact location and orientation within the map that it is believed to
# be in.
# We can also make a setting for the map to be given automatically
# so the robot just tries to figure out the location from the map
# that it was given.

# For now, return an image with the map and a new data point, with a
# vector that labels where the robot is and its orientation in said 
# space. 

import a2_map
import a2_motion
import os
import matplotlib
import math
import argparse

# Get the map that is most likely to be the robot's (by default None, otherwise just return the map given)
def get_map(map_root=None):
    if(map_root != None):
        return map_root
    

    # Perform a scan of the nearby area, and look through the area to see if anything
    # matches any part of any map (start with smaller maps, and work upward, to save on 
    # scan time if it is the smaller map.
    


    # return approx_map # Return the map that is approximated to be the one the robot is in.

def draw_location(approx_map):
