import osmium
import pandas as pd

# List of highway types to count
Highway_list = ['cycleway', 'footway', 'path', 'track', 'pedestrian']

# Class to count unique users handling ways with specified highway types
class MapperCounterHandler(osmium.SimpleHandler):
    def __init__(self):
        osmium.SimpleHandler.__init__(self)
        self.users = set()

    def way(self, w):
        # Check if the way has one of the specified highway types
        if 'highway' in w.tags and w.tags['highway'] in Highway_list:
            self.users.add(w.user)

# Class to count highway edits per user for specified highway types
class HighwayCounterHandler(osmium.SimpleHandler):
    def __init__(self, users, all_versions):
        osmium.SimpleHandler.__init__(self)
        # Initialize a DataFrame with columns as highway types and index as users
        self.result = pd.DataFrame(0, columns=Highway_list, index=list(users))
        self.total_per_user = pd.DataFrame(0, columns=['Total'], index=list(users))  # Added for total counts
        self._all_versions = all_versions
        self._way_ids = set()

    def way(self, w):
        # Check if the way has one of the specified highway types
        if 'highway' in w.tags and w.tags['highway'] in Highway_list:
            # Increment the count for the user and highway type
            if self._all_versions or w.id not in self._way_ids:
                self.result.at[w.user, w.tags['highway']] += 1
                self.total_per_user.at[w.user, 'Total'] += 1  # Increment total count
                self._way_ids.add(w.id)

# Replace this with your actual PBF file and output file paths
pbf_file = 'belgium-latest-internal.osm.pbf'
output = 'output.csv'
all_versions = False

print(f"Processing {pbf_file}")

# Stage 1: Count Unique Highway Users
print("Stage 1: Counting Unique Highway Users")
mch = MapperCounterHandler()
mch.apply_file(pbf_file)
print(f"Done, {len(mch.users)} users counted.")

# Stage 2: Count Highway Edits
print("Stage 2: Counting Highway Edits")
hch = HighwayCounterHandler(users=mch.users, all_versions=all_versions)
hch.apply_file(pbf_file)

# Concatenate the result DataFrame and total_per_user DataFrame horizontally
result_with_total = pd.concat([hch.result, hch.total_per_user], axis=1)

# Sort the DataFrame by the 'Total' column in descending order
result_with_total = result_with_total.sort_values(by='Total', ascending=False)

# Write the sorted result to a CSV file
result_with_total.to_csv(output)
print(f"Done. Result written to {output}")
