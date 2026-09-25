import os
import sys
import requests
import asyncio
import subprocess


async def get_weather(region="Belgrade", units="METRIC"):
    try:
        import python_weather as pw
    except ImportError:
        return None

    # Get the unit names to be normalized:
    units_new = units.lower()
    if(units_new == "imperial"):
        units_new = pw.IMPERIAL
        units = "Farenheit"
    elif (units_new == "metric"):
         units_new = pw.METRIC
         units = "Celsius"


    # Get weather of region:
    async with pw.Client(unit=units_new) as client:
            weather = await client.get(region)
            # Return the region (again, just in case) and temperature of the region for the AI to read out loud (will not even need to use LLM for this.)
            return region, weather.temperature

