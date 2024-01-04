## This script search notes from OpenStreetMap Api save it to a csv and make a post process based on "dates and text"  AND create a "input" file for the ORS optimization script
## Run this script and run https://github.com/PasLoin/Osm-python-analyse_Belgium/blob/main/highway/routing_optimisation/simple_ORS_Vroom_Optimization.py
## to have a custom gpx for making notes survey.
## Limited to 58 notes maximum due to limitation of ORS API. 

import requests
import csv
import pandas as pd
import time

class OpenStreetMapAPI:
    def __init__(self, bbox):
        self.bbox = bbox
        self.url = f'https://api.openstreetmap.org/api/0.6/notes.json?bbox={self.bbox[1]},{self.bbox[0]},{self.bbox[3]},{self.bbox[2]}&limit=250closed=false'
        self.data = None

    def fetch_data(self):
        response = requests.get(self.url)
        self.data = response.json()
        #print(self.data)

class CSVWriter:
    def __init__(self, data, filename):
        self.data = data
        self.filename = filename

    def write_to_csv(self):
        with open(self.filename, 'w', newline='', encoding='utf-8') as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow(['id', 'lat', 'lon', 'date', 'text'])
            for note in self.data['features']:
                writer.writerow([note['properties']['id'], note['geometry']['coordinates'][1], note['geometry']['coordinates'][0], note['properties']['date_created'], note['properties']['comments'][0]['text']])
        print(f'{self.filename} generated')

class DataFrameProcessor:
    def __init__(self, filename):
        self.df = pd.read_csv(filename)

    def get_user_input(self):
        start_date = input("Please enter the start date (dd/mm/yyyy): ")
        end_date = input("Please enter the end date (dd/mm/yyyy): ")
        search_string = input("Please enter the text to search for: ")
        return start_date, end_date, search_string



    def filter_data(self, start_date, end_date, search_string):
        # Convert dates to datetime format and set timezone to UTC
        start_date = pd.to_datetime(start_date, dayfirst=True).tz_localize('UTC')
        end_date = pd.to_datetime(end_date, dayfirst=True).tz_localize('UTC')

        # Convert 'date' column to datetime and convert timezone to UTC
        self.df['date'] = pd.to_datetime(self.df['date']).dt.tz_convert('UTC')

        # Filter based on date range
        self.df = self.df[(self.df['date'] >= start_date) & (self.df['date'] <= end_date)]

        # Filter based on text content
        self.df = self.df[self.df['text'].str.contains(search_string, na=False)]
    def process_dataframe(self):
        self.df = self.df.head(58)
        self.df = self.df[['id', 'lon', 'lat']]
        self.df['Open_From'] = pd.Timestamp.now().strftime('%Y-%m-%d 00:01:00')
        self.df['Open_To'] = pd.Timestamp.now().strftime('%Y-%m-%d 23:59:00')
        self.df['Needed_Amount'] = 1
        self.df = self.df.rename(columns={'id': 'ID', 'lon': 'Lon', 'lat': 'Lat'})

    def save_dataframe(self, filename):
        self.df.to_csv(filename, index=False)
        print(f'The {filename} file has been generated.')


bbox = (50.76, 4.24, 50.92, 4.53)
api = OpenStreetMapAPI(bbox)
api.fetch_data()

csv_writer = CSVWriter(api.data, 'notes.csv')
csv_writer.write_to_csv()

df_processor = DataFrameProcessor('notes.csv')
start_date, end_date, search_string = df_processor.get_user_input()
df_processor.filter_data(start_date, end_date, search_string)
df_processor.process_dataframe()
df_processor.save_dataframe('input.csv')
