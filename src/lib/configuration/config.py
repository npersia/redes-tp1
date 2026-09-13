import json
import os


BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def load_config(filename):
    config_filepath = os.path.join(BASE_DIR, filename)
    with open(config_filepath, "r", encoding="utf-8") as config_file:
        return json.load(config_file)