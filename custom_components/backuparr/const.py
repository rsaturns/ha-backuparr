"""Constants for the Backuparr integration."""

from datetime import timedelta
import logging

DOMAIN = "backuparr"
LOGGER = logging.getLogger(__package__)

# Backuparr's own backup reports under this id, next to the apps.
SELF_ID = "backuparr"

DEFAULT_VERIFY_SSL = True
UPDATE_INTERVAL = timedelta(minutes=5)
REQUEST_TIMEOUT = 10

STATUS_PATH = "/api/ha/status"
