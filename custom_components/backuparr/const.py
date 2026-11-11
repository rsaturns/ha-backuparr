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

# The trusted certificate of a Backuparr whose certificate Home Assistant
# can't check (its own self-signed one): SHA-256 of the certificate and of
# its public key, in the config entry's data.
CONF_CERT_SHA256 = "certificate_sha256"
CONF_KEY_SHA256 = "public_key_sha256"
