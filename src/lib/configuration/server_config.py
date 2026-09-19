import os

from lib.configuration.config import load_config as load_env_config



def load_config():
    return load_env_config("config_server.env")


def get_output_filepath(arguments, config):
    storage_path = arguments.storage
    if not os.path.isabs(storage_path):
        storage_path = os.path.abspath(storage_path)
    os.makedirs(storage_path, exist_ok=True)
    return os.path.join(storage_path, config["OUTPUT_FILENAME"])