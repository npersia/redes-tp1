import os

from lib.configuration.config import BASE_DIR, load_config as load_json_config



def load_config():
    return load_json_config("config_server.json")


def get_output_filepath(arguments, config):
    storage_path = arguments.storage
    if not os.path.isabs(storage_path):
        storage_path = os.path.abspath(storage_path)
    os.makedirs(storage_path, exist_ok=True)
    return os.path.join(storage_path, config["output_filename"])