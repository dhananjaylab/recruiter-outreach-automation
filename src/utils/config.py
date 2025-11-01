# FILE: src/utils/config.py

import os
from dotenv import load_dotenv

class ConfigLoader:
    """
    ConfigLoader is a utility class for loading environment variables from a .env file.
    """
    def __init__(self, dotenv_path=".env"):
        if not os.path.exists(dotenv_path):
            raise FileNotFoundError(f".env file not found at path: {dotenv_path}. Please create one.")
        load_dotenv(dotenv_path)

    def get(self, key, default=None):
        """
        Retrieve the value of an environment variable.
        """
        return os.getenv(key, default)