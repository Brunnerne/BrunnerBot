import logging
import os
import sys

from pathlib import Path


def parse_variable(variable, vartype, default=None, required=False):
    value = os.getenv(variable, None)
    if not value:
        if required:
            logging.fatal("Missing required environment variable: %s", variable)
            sys.exit(1)
        return default

    if vartype == str:
        return value
    if vartype == bool:
        return value.lower() in ["true", "1", "t", "y", "yes"]
    if vartype == int:
        return int(value) if value.isdigit() else default
    return default


BACKUPS_DIR_DEFAULT = (Path(__file__).parent.parent / "backups").resolve()

class Config:
    def __init__(self):
        # Required
        self.bot_token = parse_variable("BOT_TOKEN", str, required=True)

        # Options
        self.guild_id = parse_variable("GUILD_ID", int)
        self.mongodb_uri = parse_variable("MONGODB_URI", str, default="mongodb://localhost:27017")
        self.mongodb_db = parse_variable("MONGODB_DB", str, default="brunnerbot")
        self.backups_dir = parse_variable("BACKUPS_DIR", str, default=BACKUPS_DIR_DEFAULT)

        # Optional: forward CTF exports to a ctf-chat-explorer instance.
        # If either is unset, the export still runs locally but no upload is attempted.
        self.explorer_url = parse_variable("EXPLORER_URL", str)
        self.explorer_token = parse_variable("EXPLORER_TOKEN", str)


config = Config()
