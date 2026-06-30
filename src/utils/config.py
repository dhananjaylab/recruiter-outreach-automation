# FILE: src/utils/config.py

import os
from pathlib import Path
from dotenv import load_dotenv


class ConfigLoader:
    """
    Loads environment variables from a .env file.

    Fix applied vs original:
    - Constructor now raises ValueError (not FileNotFoundError) so that
      main.py's `except ValueError` block actually catches it.
    - Strips surrounding quotes from values (common .env authoring mistake).
    """

    def __init__(self, dotenv_path: str = ".env"):
        path = Path(dotenv_path)
        if not path.exists():
            raise ValueError(
                f".env file not found at '{dotenv_path}'. "
                "Please create one using the template in README.md."
            )
        load_dotenv(dotenv_path, override=True)

    def get(self, key: str, default: str | None = None) -> str | None:
        """Returns env var value, stripping accidental surrounding quotes."""
        value = os.getenv(key, default)
        if isinstance(value, str):
            value = value.strip().strip('"').strip("'")
        return value if value else default
