import os

from .deployment_vars import *
TEST = True if os.environ.get('PYTEST_VERSION') else False

REFRESH_CREDENTIAL_INTERVAL_MINUTES = 30

ORIGINS = [
    "https://*.ca.gov",
    "https://maps.conservation.ca.gov",
    "https://docgis.conservation.ca.gov",
    "https://gis.conservation.ca.gov",
    "https://gisportal.co.fresno.ca.us",
    "https://gispublic.waterboards.ca.gov",
    "https://california.maps.arcgis.com",
    "https://egis.fire.ca.gov",
    "https://calfire-forestry.maps.arcgis.com"
]