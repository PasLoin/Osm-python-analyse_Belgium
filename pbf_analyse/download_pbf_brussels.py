#!/usr/bin/python3
import requests
from datetime import datetime
import os

url = "https://osmtoday.com/europe/belgium/brussels_capital_region.pbf"
r = requests.get(url)
current_date = datetime.now().strftime("%d_%m_%Y")
os.makedirs("history", exist_ok=True)
filename = f"pbf_analyse/history/{current_date}_brussels_capital_region.pbf"
with open(filename, "wb") as f:
    f.write(r.content)
