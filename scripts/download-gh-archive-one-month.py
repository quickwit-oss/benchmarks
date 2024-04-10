import requests
import gzip
import argparse

parser = argparse.ArgumentParser(description='Downloads one months worth of the GitHub archive dataset.')
parser.add_argument(
    '-o',
    '--output',
    default="gharchive.json",
    metavar='PATH',
    type=str,
    help='The output file path to export the produced file.',
)
parser.add_argument(
    '-y',
    '--year',
    default=2023,
    metavar='PATH',
    type=int,
    help='The year to select data from.',
)

args = parser.parse_args()

total_bytes = 0
print("Downloading January 2023 archive")
with open(args.output, "wb+") as file:
    for day in range(1, 32):
        date = f"{args.year}-01-{day:02}"
        for hour in range(0, 24):
            r = requests.get(f"https://data.gharchive.org/{date}-{hour}.json.gz")
            r.raise_for_status()

            data = gzip.decompress(r.content)
            total_bytes += len(data)
            file.write(data)
        print(f"Fetched {date}")

total_gb = total_bytes / (1 << 30)
print(f"Downloaded {total_gb:.2f}GB dataset")
