import osmium
import pandas as pd
import matplotlib.pyplot as plt

# List of highway types to count
Highway_list = ['cycleway', 'footway', 'path', 'track', 'pedestrian']

# Class to count unique users handling ways with specified highway types
class MapperCounterHandler(osmium.SimpleHandler):
    def __init__(self):
        osmium.SimpleHandler.__init__(self)
        self.user_mapping = {}

    def way(self, w):
        # Check if the way has one of the specified highway types
        if 'highway' in w.tags and w.tags['highway'] in Highway_list:
            # Map original username to an anonymized username
            if w.user not in self.user_mapping:
                self.user_mapping[w.user] = f'User{len(self.user_mapping) + 1}'

# Class to count highway edits per user for specified highway types
class HighwayCounterHandler(osmium.SimpleHandler):
    def __init__(self, user_mapping, all_versions):
        osmium.SimpleHandler.__init__(self)
        self.user_mapping = user_mapping  # Save the user_mapping attribute
        # Initialize a DataFrame with columns as highway types and index as users
        self.result = pd.DataFrame(0, columns=Highway_list, index=list(user_mapping.values()))
        self.total_per_user = pd.DataFrame(0, columns=['Total'], index=list(user_mapping.values()))
        self._all_versions = all_versions
        self._way_ids = set()

    def way(self, w):
        # Check if the way has one of the specified highway types
        if 'highway' in w.tags and w.tags['highway'] in Highway_list:
            # Get anonymized username from the mapping
            user = self.user_mapping.get(w.user, None)
            if user:
                # Increment the count for the user and highway type
                if self._all_versions or w.id not in self._way_ids:
                    self.result.at[user, w.tags['highway']] += 1
                    self.total_per_user.at[user, 'Total'] += 1  # Increment total count
                    self._way_ids.add(w.id)

# Replace this with your actual PBF file and output file paths
pbf_file = 'belgium-latest-internal.osm.pbf'
output = 'soft_mobility_users_Belgium_anonymized.csv'
all_versions = False

print(f"Processing {pbf_file}")

# Stage 1: Count Unique Highway Users
print("Stage 1: Counting Unique Highway Users")
mch = MapperCounterHandler()
mch.apply_file(pbf_file)
print(f"Done, {len(mch.user_mapping)} users counted.")

# Stage 2: Count Highway Edits
print("Stage 2: Counting Highway Edits")
hch = HighwayCounterHandler(user_mapping=mch.user_mapping, all_versions=all_versions)
hch.apply_file(pbf_file)

# Concatenate the result DataFrame and total_per_user DataFrame horizontally
result_with_total = pd.concat([hch.result, hch.total_per_user], axis=1)

# Sort the DataFrame by the sum of counts across all highway types in descending order
result_with_total = result_with_total.loc[result_with_total.sum(axis=1).sort_values(ascending=False).index]

# Write the sorted result to a CSV file
result_with_total.to_csv(output)
print(f"Done. Result written to {output}")

# Plot a bar chart for the distribution per way type for the top users
fig, ax = plt.subplots(figsize=(12, 6))
top_users = result_with_total.head(10)
top_users[Highway_list].plot(kind='bar', stacked=True, ax=ax)
plt.xlabel('Users')
plt.ylabel('Count')
plt.title('Top 10 Users - Edits Distribution per Way Type')
plt.legend(title='Way Type', bbox_to_anchor=(1.05, 1), loc='upper left')
plt.tight_layout()

# Plot a pie chart for the percentage of total edits per user for the top users
# plt.figure(figsize=(8, 8))
# percentage_per_user = (top_users['Total'] / top_users['Total'].sum()) * 100
# percentage_per_user.plot(kind='pie', autopct='%1.1f%%', startangle=90, colors=plt.cm.Paired.colors)
# plt.title('Top 10 Users - Percentage of Total Edits')
# plt.tight_layout()

# Show the plots
plt.show()
