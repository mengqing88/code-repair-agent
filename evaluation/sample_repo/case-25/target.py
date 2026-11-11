import csv

def names(text):
    return [row[0] for row in csv.reader(text.splitlines())]
