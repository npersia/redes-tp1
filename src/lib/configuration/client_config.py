from lib.configuration.config import load_config as load_env_config


def load_config():
    return load_env_config("config_client.env")