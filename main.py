#from azurefunctions.extensions.http.fastapi import Request, StreamingResponse, Response

__version__ = "2025.07.30a"

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, StreamingResponse, Response

from base64 import b64decode
from hashlib import sha256
import datetime

from hexprox import hexagon
from hexprox.hexagon import HexagonManager, HEXAGON_TILE_EXTENSIONS

from hexprox.config import DEBUG
STREAM_CHUNK_SIZE = 256000 if not DEBUG else 4096  # requests package blocks for the full read - probably OK for larger in production because instances will get spun up. May want lower in dev

# salt will just be for in-memory - we're not storing anything, but just to help
SALT = datetime.datetime.now(tz=datetime.UTC).strftime("%Y%m%d%H%M%S")

def get_hash(client_id, client_secret, salt=SALT):
    return sha256(f"{client_id}:{client_secret}:{salt}".encode("utf-8")).hexdigest()  # hexdigest formats the hash as a string we can store



app = FastAPI()

CLIENTS = {}

def get_client(client_id, client_secret):
    client_id = b64decode(client_id).decode("utf-8")
    client_secret = b64decode(client_secret).decode("utf-8")

    print(client_id)
    print(client_secret)
    client_hash = get_hash(client_id, client_secret)  # this way there's no good way to get the secrets from this object, even if they're stored on the individual objects - probably overkill

    # find out if we already have a client for this user - if so, use it - we could end up racing if they hit us with a bunch of requests though
    if client_hash in CLIENTS:
        client = CLIENTS[client_hash]
    else:
        # When we create the manager, strip off any spaces in the client ID and secret - don't do it earlier - that way we can skip the replace on re-used requests
        client = HexagonManager(client_id=client_id.replace(" ", ""), client_secret=client_secret.replace(" ", ""), wmts_url=hexagon.STREAMING_WMTS_URL)
        CLIENTS[client_hash] = client

    return client

@app.get("/")
async def root():
    return {"message": f"HexProx is up, version {__version__}"}

@app.get("/v1/wmts/{api_key}/{client_id}/{client_secret}/1.0.0/HxGN_Imagery/default/WebMercator/{matrix}/{row}/{col}.{ext}")
async def get_wmts(api_key: str, client_id: str, client_secret: str, matrix: int, row: int, col: int, ext: str, request: Request):

    if ext not in HEXAGON_TILE_EXTENSIONS:
        return Response(status_code=404, content=f"File extension {ext} not supported")

    try:
        client = get_client(client_id, client_secret)
    except PermissionError:
        return Response(status_code=403, content="Invalid credentials or inability to communicate with credential server")

    if "Origin" in request.headers and ("ca.gov" in request.headers["Origin"] or "arcgis.com" in request.headers["Origin"]): # trying to catch if this is in a web browser rather than a desktop client  # and "arcgis.com" in request.headers["Origin"]:  # this likely applies if *any* Origin is included since it's a CORS issue that causes us to need to stream it
        # we may still want to open this check up, but trying to limit it so we don't pay out tiles for random people's web maps if they happen to capture a URL.
        # when invoked via a request from a browser, we get CORS issues unless we proxy the tile data too, but it's slower and costs more, so we want to avoid it when possible
        try:
            response = client.get_tile(matrix=matrix, row=row, col=col, stream=True, url_only=False)
        except PermissionError:
            return Response(status_code=403, content="Invalid credentials or inability to communicate with credential server")

        data = response.content
        return Response(content=data, status_code=200, media_type="image/png")
        #def iter_response():
        #    for data in response.iter_content(chunk_size=STREAM_CHUNK_SIZE):
        #        yield data

        #return StreamingResponse(iter_response())
    else:
        return RedirectResponse(url=client.get_tile(matrix=matrix, row=row, col=col, url_only=True))

@app.get("/v1/wmts/{api_key}/{client_id}/{client_secret}/{rest_of_path:path}")
async def get_wmts_general(api_key: str, client_id: str, client_secret: str, rest_of_path: str, request: Request) -> Response:
    try:
        client = get_client(client_id, client_secret)
        response = client.get_general_response(rest_of_path)
    except PermissionError:  # this is still too coarse - we should raise better errors in Hexagon.py to differentiate here.
        return Response(status_code=403, content="Invalid credentials or inability to communicate with credential server")

    if "content-encoding" in response.headers:
        del response.headers["content-encoding"]

    current_base_url = f"{request.base_url}wmts/{api_key}/{client_id}/{client_secret}/"
    rewritten_content = response.content.decode("utf-8").replace("https://services.hxgncontent.com/streaming/wmts?/", current_base_url)

    return Response(status_code=response.status_code,
                    #headers=response.headers,  # we may still want to translate *some* of these over
                    media_type="application/xml",
                    content=rewritten_content)