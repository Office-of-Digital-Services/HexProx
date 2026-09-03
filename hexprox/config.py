import os
import json

from .deployment_vars import *
TEST = True if os.environ.get('PYTEST_VERSION') else False

REFRESH_CREDENTIAL_INTERVAL_MINUTES = 30

ORIGINS = json.loads(os.environ.get("ORIGINS", '{"origins":[]}'))["origins"]

ORIGINS_REGEX = "https://.*\.ca\.gov"