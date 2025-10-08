import datetime
import json
import logging
import random

from fastapi import BackgroundTasks, HTTPException
from fastapi import Request

try:
    from azure.core import exceptions as azure_exceptions
    from azure.keyvault.secrets import SecretClient
except:
    logging.error("Can't import Azure library in key_manager")
    raise

from hexprox import config


class APIKeyManager:

    def __init__(self):
        self.api_keys = {}

    async def refresh_credentials(self, api_key: str, key_vault_client: SecretClient):
        refresh_time = datetime.datetime.now(tz=datetime.UTC)
        if refresh_time > self.api_keys[api_key]['last_refreshed'] + datetime.timedelta(minutes=config.REFRESH_CREDENTIAL_INTERVAL_MINUTES):
            await self._retrieve_credentials(api_key, key_vault_client=key_vault_client)

    async def _retrieve_credentials(self, api_key: str, key_vault_client: SecretClient):
        self.api_keys[api_key] = json.loads(key_vault_client.get_secret(f"credential-set-{api_key}").value)
        self.api_keys[api_key]['last_refreshed'] = datetime.datetime.now(tz=datetime.UTC)  # mark when we last retrieved these

    async def get_credentials_for_api_key(self, api_key: str, key_vault_client: SecretClient, background_tasks: BackgroundTasks, request: Request) -> dict:
        """
            Credential sets should have the structure of the form:
            {
                "count": 3,
                "sets": [
                    {"client_id": "value", "client_secret": "value"},
                    {"client_id": "value", "client_secret": "value"},
                    {"client_id": "value", "client_secret": "value"}
                ],
                "org": "Org name",
                "contact": "Contact name"
            }

            This rotates between these sets in a random manner
        :param api_key:
        :return:
        """
        try:
            if api_key not in self.api_keys:  # if we haven't already cached the credentials for this API key locally, then do it now
                print("retrieving credentials for api key from key vault")
                await self._retrieve_credentials(api_key, key_vault_client=key_vault_client)
            else:  # if it's already there, schedule a refresh for after the request is complete - it'll only actually refresh at specific intervals.
                background_tasks.add_task(self.refresh_credentials, api_key, key_vault_client)

            if api_key not in self.api_keys:  # if it's *still* not there, then the credentials were invalid
                raise HTTPException(status_code=403, detail="Invalid API key or API key lacks permissions for this resource")

            credential_set = self.api_keys[api_key]
        except azure_exceptions.ResourceNotFoundError:
            raise HTTPException(status_code=403, detail="Invalid API key, malformed secret data, or API key lacks permissions for this resource")

        if type(credential_set) is not dict or "count" not in credential_set:
            raise HTTPException(status_code=403, detail="Invalid API key, malformed secret data, or API key lacks permissions for this resource")

        num_sets = credential_set['count']   # we may store multiple credentials - rather than running a length operation each time, just pull the stored value
        if num_sets > 1:
            random.seed() # uses the time by default - this doesn't need to be secure, just trying to distribute credential requests
            index = random.randint(0, num_sets-1)  # get which set to use - this is inclusive of both ends of the range, but we're going to index a list, so need to drop 1
        else:
            index = 0

        if "org" in credential_set:
            properties = {'custom_dimensions': {'organization_from_key': credential_set['org']}}
            logging.info('Processing request', extra=properties)


        logging.debug(f"credential index: {index} of {num_sets} sets")
        return credential_set['sets'][index]
