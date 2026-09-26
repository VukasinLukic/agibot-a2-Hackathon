import time
import datetime
import zoneinfo
import os

continents = ["Africa", "America", "Asia", "Atlantic", "Australia", "Europe", "Indian", "Pacific"]

def get_time(city="Belgrade"):

    for i in range(len(continents)):
        try:
            full_region = str(continents[i] + "/" + city)
            time_zone = zoneinfo.ZoneInfo(full_region)
            current_time = datetime.datetime.now(time_zone)
            # current_time_written = current_time.strftime("%H:%M") --> This gives the correct time (For ex: 10:30), but the robot will say the : as "double points", which will be easier to just do seperately.
            current_time_hr = current_time.strftime("%H")
            current_time_min = current_time.strftime("%M")
            current_time_written = current_time.strftime("%H:%M")
            

            # Potentially what we could do, which would be pretty sick is to display the time 
            # in the tiny screen so people can see physically, in which case we'd also want to 
            # add the original method and return it alongside the hr and minute. 
            return current_time_hr, current_time_min, current_time_written
        except Exception as e:
            continue # If there is no country in the continent on the list, contiue.

    return None # If there is no city of that name, then just return None, and have an outcome for when the city does not exist (Ask for clarification/Tell them "The city: {city} does not exist.")

if __name__ == "__main__":
    # Guarded so importing this from the agent doesn't print on every worker
    # start. Still runnable as `python tell_time.py` for a quick check.
    time_ans = get_time()
    print(time_ans)