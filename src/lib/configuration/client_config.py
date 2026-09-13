from lib.configuration.config import load_config as load_json_config


def load_config():
    return load_json_config("config_client.json")