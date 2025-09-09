#from azurefunctions.extensions.http.fastapi import Request, StreamingResponse, Response

__version__ = "2025.09.09a"
__author__ = "Nick Santos"
__license__ = "MIT"
__copyright__ = "Copyright 2025 California Department of Technology"
__status__ = "Development"
__description__ = "A proxy service for Hexagon imagery that supports WMTS and WMS requests."

import random
import os
import json

from azure.keyvault.secrets import SecretClient
from azure.identity import DefaultAzureCredential, ManagedIdentityCredential

from fastapi import FastAPI, Request, BackgroundTasks, HTTPException
from fastapi.responses import RedirectResponse, StreamingResponse, Response
from fastapi.middleware.cors import CORSMiddleware

from base64 import b64decode
from hashlib import sha256
import datetime

from hexprox import hexagon, config
from hexprox.hexagon import HexagonManager, HEXAGON_TILE_EXTENSIONS

from hexprox.config import DEBUG
STREAM_CHUNK_SIZE = 256000 if not DEBUG else 4096  # requests package blocks for the full read - probably OK for larger in production because instances will get spun up. May want lower in dev

# salt will just be for in-memory - we're not storing anything, but just to help
SALT = datetime.datetime.now(tz=datetime.UTC).strftime("%Y%m%d%H%M%S")

def get_hash(client_id, client_secret, salt=SALT):
    return sha256(f"{client_id}:{client_secret}:{salt}".encode("utf-8")).hexdigest()  # hexdigest formats the hash as a string we can store
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.ORIGINS,
    allow_credentials=True,
    allow_methods=["HEAD", "GET", "OPTIONS"],
    allow_headers=["*"],
)

CLIENTS = {}

API_KEYS = {}

try:
    KEY_VAULT_NAME = os.environ["KEY_VAULT_NAME"]
    KEY_VAULT_URI = f"https://{KEY_VAULT_NAME}.vault.azure.net"

    AZURE_CREDENTIAL = DefaultAzureCredential() #ManagedIdentityCredential()
    KEY_VAULT_CLIENT = SecretClient(vault_url=KEY_VAULT_URI, credential=AZURE_CREDENTIAL)
    print("Checkpoint - key vault loaded")
except:
    print("error loading key vault")
    # this is just for debug and needs to be removed

def get_client(client_id, client_secret, api_version="v2"):
    global CLIENTS
    if api_version == "v1":  # running the replace operation here slows things down relative to if it was post-hash in the client, but that's fine because we expect to phase out v1
        client_id = b64decode(client_id).decode("utf-8").replace(" ", "")
        client_secret = b64decode(client_secret).decode("utf-8").replace(" ", "")

    client_hash = get_hash(client_id, client_secret)  # makes the actual credentials be a bit deeper in the memory structure - even if they're stored on the individual objects - probably overkill

    # find out if we already have a client for this user - if so, use it - we could end up racing if they hit us with a bunch of requests though
    if client_hash in CLIENTS:
        client = CLIENTS[client_hash]
    else:
        client = HexagonManager(client_id=client_id, client_secret=client_secret, wmts_url=hexagon.STREAMING_WMTS_URL)
        CLIENTS[client_hash] = client

    return client
@app.get("/")
async def root_get():
    return {"message": f"Service is up"}

@app.get("/about/{api_key}")
async def about_page(api_key: str, background_tasks: BackgroundTasks):
    await get_credentials_for_api_key(api_key, background_tasks)  # we don't actually need the creds, we just want to make sure the API key is valid
    return {"message": f"Service is up. Version {__version__}"}

@app.get("/v1/wmts/{api_key}/{client_id}/{client_secret}/1.0.0/HxGN_Imagery/default/WebMercator/{matrix}/{row}/{col}.{ext}")
async def get_wmts_tile(api_key: str, client_id: str, client_secret: str, matrix: int, row: int, col: int, ext: str, request: Request):
    return await get_wmts_tile_response("v1", client_id, client_secret, col, ext, matrix, request, row)

@app.get("/v2/wmts/{api_key}/1.0.0/HxGN_Imagery/default/WebMercator/{matrix}/{row}/{col}.{ext}")
async def get_wmts_tile_v2(api_key: str, matrix: int, row: int, col: int, ext: str, request: Request, background_tasks: BackgroundTasks):
    credentials = await get_credentials_for_api_key(api_key, background_tasks)
    return await get_wmts_tile_response("v2", credentials['client_id'], credentials['client_secret'], col, ext, matrix, request, row)


async def get_wmts_tile_response(api_version, client_id, client_secret, col, ext, matrix, request, row):
    if ext not in HEXAGON_TILE_EXTENSIONS:
        return Response(status_code=404, content=f"File extension {ext} not supported")
    try:
        client = get_client(client_id, client_secret, api_version=api_version)
    except PermissionError:
        return Response(status_code=403,
                        content="Invalid credentials or inability to communicate with credential server")
    if "Origin" in request.headers and ("ca.gov" in request.headers["Origin"] or "arcgis.com" in request.headers[
        "Origin"]):  # trying to catch if this is in a web browser rather than a desktop client  # and "arcgis.com" in request.headers["Origin"]:  # this likely applies if *any* Origin is included since it's a CORS issue that causes us to need to stream it
        # we may still want to open this check up, but trying to limit it so we don't pay out tiles for random people's web maps if they happen to capture a URL.
        # when invoked via a request from a browser, we get CORS issues unless we proxy the tile data too, but it's slower and costs more, so we want to avoid it when possible
        try:
            response = client.get_tile(matrix=matrix, row=row, col=col, stream=True, url_only=False)
        except PermissionError:
            return Response(status_code=403,
                            content="Invalid credentials or inability to communicate with credential server")

        data = response.content
        print("Returning fully proxied data response")
        return Response(content=data,
                        status_code=200,
                        media_type="image/png")
    else:
        return RedirectResponse(url=client.get_tile(matrix=matrix, row=row, col=col, url_only=True))


@app.get("/v1/wmts/{api_key}/{client_id}/{client_secret}/{rest_of_path:path}")
async def get_wmts_general(api_key: str, client_id: str, client_secret: str, rest_of_path: str, request: Request) -> Response:
    return await credentialed_wmts_service_response(api_key, "v1", client_id, client_secret, request,
                                                   rest_of_path)
@app.get("/v2/wmts/{api_key}/{rest_of_path:path}")
async def get_wmts_general_v2(api_key: str, rest_of_path: str, request: Request, background_tasks: BackgroundTasks) -> Response:
    credentials = await get_credentials_for_api_key(api_key, background_tasks)
    return await credentialed_wmts_service_response(api_key, "v2", credentials['client_id'], credentials['client_secret'], request,
                                                   rest_of_path)

async def credentialed_wmts_service_response(api_key, api_version, client_id, client_secret, request, rest_of_path):
    try:
        client = get_client(client_id, client_secret, api_version=api_version)
        response = client.get_general_response(rest_of_path, params=request.query_params)
    except PermissionError:  # this is still too coarse - we should raise better errors in Hexagon.py to differentiate here.
        return Response(status_code=403,
                        content="Invalid credentials or inability to communicate with credential server")
    if "content-encoding" in response.headers:
        del response.headers["content-encoding"]

    if api_version == "v1":
        current_base_url = f"{request.base_url}{api_version}/wmts/{api_key}/{client_id}/{client_secret}/"
    else:
        current_base_url = f"{request.base_url}{api_version}/wmts/{api_key}/"

    rewritten_content = response.content.decode("utf-8").replace("https://services.hxgncontent.com/streaming/wmts?/",
                                                                 current_base_url)
    return Response(status_code=response.status_code,
                    # headers=response.headers,  # we may still want to translate *some* of these over
                    media_type="application/xml",
                    content=rewritten_content)


async def refresh_credentials(api_key: str):
    refresh_time = datetime.datetime.now(tz=datetime.UTC)
    if refresh_time > API_KEYS[api_key]['last_refreshed'] + datetime.timedelta(minutes=config.REFRESH_CREDENTIAL_INTERVAL_MINUTES):
        await _retrieve_credentials(api_key)

async def _retrieve_credentials(api_key: str, key_vault_client=KEY_VAULT_CLIENT):
    global API_KEYS
    API_KEYS[api_key] = json.loads(key_vault_client.get_secret(f"credential-set-{api_key}").value)
    API_KEYS[api_key]['last_refreshed'] = datetime.datetime.now(tz=datetime.UTC)  # mark when we last retrieved these

async def get_credentials_for_api_key(api_key: str, background_tasks: BackgroundTasks) -> dict:
    """
        Credential sets should have the structure of the form:
        {
            'count': 3,
            'sets': [
                {'client_id': 'value', 'client_secret': 'value'},
                {'client_id': 'value', 'client_secret': 'value'},
                {'client_id': 'value', 'client_secret': 'value'},
            ]
        }

        This rotates between these sets in a random manner
    :param api_key:
    :return:
    """
    global API_KEYS

    if api_key not in API_KEYS:  # if we haven't already cached the credentials for this API key locally, then do it now
        print("retrieving credentials for api key from key vault")
        await _retrieve_credentials(api_key)
    else:  # if it's already there, schedule a refresh for after the request is complete - it'll only actually refresh at specific intervals.
        background_tasks.add_task(refresh_credentials, api_key)

    if api_key not in API_KEYS:  # if it's *still* not there, then the credentials were invalid
        raise HTTPException(status_code=403, detail="Invalid API Key or API Key lacks permissions for this resource")

    credential_set = API_KEYS[api_key]

    if type(credential_set) is not dict or "count" not in credential_set:
        raise HTTPException(status_code=403, detail="Invalid API Key, malformed secret data, or API Key lacks permissions for this resource")

    num_sets = credential_set['count']   # we may store multiple credentials - rather than running a length operation each time, just pull the stored value
    if num_sets > 1:
        random.seed() # uses the time by default - this doesn't need to be secure, just trying to distribute credential requests
        index = random.randint(0, num_sets-1)  # get which set to use - this is inclusive of both ends of the range, but we're going to index a list, so need to drop 1

    print(f"credential index: {index} of {num_sets} sets")
    return credential_set['sets'][index]
