from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse, PlainTextResponse, Response

from base64 import b64decode
from hashlib import sha256
import datetime

from starlette.responses import RedirectResponse

import hexagon
from hexagon import HexagonManager

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
        client = HexagonManager(client_id=client_id, client_secret=client_secret, wmts_url=hexagon.STREAMING_WMTS_URL)
        CLIENTS[client_hash] = client

    return client

@app.get("/")
async def root():
    return {"message": "Hello World"}

@app.get("/wmts/{client_id}/{client_secret}/1.0.0/HxGN_Imagery/default/WebMercator/{matrix}/{row}/{col}.png")
async def get_wmts(client_id: str, client_secret: str, matrix: int, row: int, col: int):

    client = get_client(client_id, client_secret)

    return RedirectResponse(client.get_tile(matrix=matrix, row=row, col=col, url_only=True))

    #response = client.get_tile(matrix=matrix, row=row, col=col, stream=True)

    #def iter_response():
    #    for data in response.iter_content(chunk_size=65535):
    #        yield data

    #return StreamingResponse(iter_response(), headers=response.headers)


@app.get("/wmts/{client_id}/{client_secret}/{rest_of_path:path}", response_class=Response)
async def get_wmts_general(client_id: str, client_secret: str, rest_of_path: str, request: Request):
    client = get_client(client_id, client_secret)
    response = client.get_general_response(rest_of_path)
    print(response.headers)
    print(response.content)
    print(response.url)

    if "content-encoding" in response.headers:
        del response.headers["content-encoding"]
    current_base_url = f"{request.base_url}wmts/{client_id}/{client_secret}/"
    rewritten_content = response.content.decode("utf-8").replace("https://services.hxgncontent.com/streaming/wmts?/", current_base_url)

    r = Response(status_code=response.status_code,
                    headers=response.headers,
                    media_type="text/xml",
                    content=rewritten_content)
    return r