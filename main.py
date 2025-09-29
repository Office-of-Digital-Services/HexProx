#from azurefunctions.extensions.http.fastapi import Request, StreamingResponse, Response

__version__ = "2025.09.26a"
__author__ = "Nick Santos"
__license__ = "MIT"
__copyright__ = "Copyright 2025 California Department of Technology"
__status__ = "Development"
__description__ = "A proxy service for Hexagon imagery that supports WMTS and (in the future) WMS requests."

import os
import traceback

from fastapi import FastAPI, Request, BackgroundTasks, HTTPException
from fastapi.responses import RedirectResponse, Response
from fastapi.middleware.cors import CORSMiddleware

from base64 import b64decode
from hashlib import sha256
import datetime

from hexprox import hexagon, config
from hexprox.hexagon import HexagonManager, HEXAGON_TILE_EXTENSIONS
from hexprox.key_manager import APIKeyManager

from hexprox.config import DEBUG

try:
    from azure.keyvault.secrets import SecretClient
    from azure.core import exceptions as azure_exceptions
    from azure.identity import ManagedIdentityCredential
except:
    # this isn't correct - this part of the code runs on startup not in response to a request
    raise HTTPException(status_code=500, detail="Unable to azure secrets and identity libraries")

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

try:
    KEY_VAULT_NAME = os.environ["KEY_VAULT_NAME"]
    KEY_VAULT_URI = f"https://{KEY_VAULT_NAME}.vault.azure.net"

    managed_identity_id = os.environ["MANAGED_IDENTITY_CLIENT_ID"]
    AZURE_CREDENTIAL = ManagedIdentityCredential(client_id=managed_identity_id)
    KEY_VAULT_CLIENT = SecretClient(vault_url=KEY_VAULT_URI, credential=AZURE_CREDENTIAL)
    print("Checkpoint - key vault loaded")
except Exception as e:
    if not config.TEST: ## In the testing environment we won't connect to the secrets manager. We'll mock it out
        # this isn't correct - this part of the code runs on startup not in response to a request
        raise Exception(f"Unable to load key vault: {traceback.format_exc(e)}")
    else:
        from unittest.mock import MagicMock
        KEY_VAULT_CLIENT = MagicMock(spec=SecretClient)

try:
    BASE_URL = os.environ.get("BASE_URL", None)
except:
    BASE_URL = None

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


API_KEY_MANAGER = APIKeyManager()


@app.get("/")
async def root_get():
    return {"message": f"Service is up."}

@app.get("/about/{api_key}")
async def about_page(api_key: str, request: Request, background_tasks: BackgroundTasks):
    await API_KEY_MANAGER.get_credentials_for_api_key(api_key, KEY_VAULT_CLIENT, background_tasks, request)  # we don't actually need the creds, we just want to make sure the API key is valid
    return {"message": f"Service is up. HexProx version {__version__}"}

@app.get("/v1/wmts/{api_key}/{client_id}/{client_secret}/1.0.0/HxGN_Imagery/default/WebMercator/{matrix}/{row}/{col}.{ext}")
async def get_wmts_tile(api_key: str, client_id: str, client_secret: str, matrix: int, row: int, col: int, ext: str, request: Request):
    return await get_wmts_tile_response("v1", client_id, client_secret, col, ext, matrix, request, row)

@app.get("/v2/wmts/{api_key}/1.0.0/HxGN_Imagery/default/WebMercator/{matrix}/{row}/{col}.{ext}")
async def get_wmts_tile_v2(api_key: str, matrix: int, row: int, col: int, ext: str, request: Request, background_tasks: BackgroundTasks):
    credentials = await API_KEY_MANAGER.get_credentials_for_api_key(api_key, KEY_VAULT_CLIENT, background_tasks, request)
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
    credentials = await API_KEY_MANAGER.get_credentials_for_api_key(api_key, KEY_VAULT_CLIENT, background_tasks, request)
    return await credentialed_wmts_service_response(api_key, "v2", credentials['client_id'], credentials['client_secret'], request,
                                                   rest_of_path)

async def credentialed_wmts_service_response(api_key, api_version, client_id, client_secret, request, rest_of_path, base_url=BASE_URL):
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
        if not base_url:
            base_url = request.base_url
        current_base_url = f"{base_url}{api_version}/wmts/{api_key}/"

    rewritten_content = response.content.decode("utf-8").replace("https://services.hxgncontent.com/streaming/wmts?/",
                                                                 current_base_url)
    return Response(status_code=response.status_code,
                    # headers=response.headers,  # we may still want to translate *some* of these over
                    media_type="application/xml",
                    content=rewritten_content)
