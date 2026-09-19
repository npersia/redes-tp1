import os


BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# version con env
#  ahora lo que hace es tomar todas
#  las variables del archivo .env y cargarlas

def load_config(filename):
    config_filepath = os.path.join(BASE_DIR, filename)
    config = {}
    with open(config_filepath, "r", encoding="utf-8") as config_file:
        for line in config_file:
            line = line.strip()
            if line and not line.startswith("#"):
                key, value = line.split("=", 1)
                config[key.strip()] = value.strip()
    return config