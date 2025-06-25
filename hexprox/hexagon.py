import os
import tempfile

import requests
from datetime import datetime, UTC

TOKEN_URL = "https://services.hxgncontent.com/streaming/oauth/token?grant_type=client_credentials"

BATCH_WMTS_URL = "https://services.hxgncontent.com/orders/wmts?/"
BATCH_WMS_URL = "https://services.hxgncontent.com/orders/wms?/"
STREAMING_WMTS_URL = "https://services.hxgncontent.com/streaming/wmts?/"

PARAMS = "1.0.0/HxGN_Imagery/default/WebMercator/"

HEXAGON_TILE_EXTENSIONS = ["jpg", "png", "xxx", "txt", "html", "gml"]
# ZOOMS = {10: (list(range()))

class HexagonManager():
    def __init__(self, client_id, client_secret, wmts_url=BATCH_WMTS_URL, url_params=PARAMS, token_url=TOKEN_URL):
        self._token_info = None
        self._reauthorize_after = datetime.now(tz=UTC)
        self.token_url = token_url


        if len(client_id) > 80 or len(client_secret) > 80 or " " in client_id or " " in client_secret or ";" in client_id or ";" in client_secret:
            # some observed at 44 char, some at 24. This is just a sanity check. Also disallow spaces and semicolons to delimit statements to block attempted code injections early, without sending to Hexagon
            raise PermissionError("Invalid client ID or secret")

        self.client_id = client_id
        self.client_secret = client_secret

        self.wmts_url = wmts_url
        self.url_params = url_params

        self.session = requests.Session()

        self.default_folder = tempfile.mkdtemp(prefix="hexagon_")  # where to save tiles

    def _get_token(self):
        """Internal method - sends the request to a token URL to get the auth token to use, calculates its valid period
        Raises:
            RuntimeError: _description_
        Returns:
            _type_: _description_
        """
        full_url = f"{self.token_url}&client_id={self.client_id}&client_secret={self.client_secret}"  # not safe for untrusted inputs. Fine if we know our values
        response = requests.get(full_url)

        if response.status_code == 200:
            body = response.json()  # this has an access token and an expiration
            reauthorize_after_seconds = datetime.now(tz=UTC).timestamp() + body[
                "expires_in"] - 5  # add the expiration amount to the timestamp, then subtract 5 seconds to make sure we have a buffer
            reauthorize_dt = datetime.fromtimestamp(reauthorize_after_seconds,
                                                    tz=UTC)  # get the timestamp we'll need to reauthorize after
            body["reauthorize_after"] = reauthorize_dt
            return body
        else:
            raise PermissionError(f"Couldn't get access token, server returned status code {response.status_code} and message {response.content}")

    @property
    def token(self):
        if datetime.now(
                UTC) > self._reauthorize_after:  # if our existing token is no longer valid, or we don't have one at all
            self._token_info = self._get_token()  # authenticate for a token
            self._reauthorize_after = self._token_info[
                "reauthorize_after"]  # keep track of when we should reauthorize again in the future.
        return self._token_info["access_token"]

    def get_general_response(self, path):
        url = self.wmts_url + f"{path}&access_token={self.token}"
        return requests.get(url)

    def get_tile(self, matrix, row, col, path=None, stream=False, url_only=False, extension="png"):
        """
            Automatically composes the correct request to the WMS server for the tile, then returns the path to the downloaded tile.
            Returns the path to the downloaded tile if it downloaded one, otherwise raises the HTTP status code the server provided.
            If the server returns status code 429, then you need to wait longer between calls to this function.
        Args:
            matrix (_type_): The WMTS tile matrix (ie Zoom) to request tiles from
            row (_type_): The WMTS row within the tile matrix (ie, y)
            col (_type_): The WMTS column within the tile matrix (ie, x)
            path (str or None): The full output path for the downloaded tile, including tile extension. If None, then a path in Temp will be generated.
        """
        filename = os.path.join(str(matrix), str(row), f"{col}.{extension}")
        file_url = f"{matrix}/{row}/{col}.{extension}"
        url = self.wmts_url + self.url_params + f"{file_url}&access_token={self.token}"
        print(f"fetching {url}")

        if url_only:  # this is for when we proxy via redirect
            return url

        response = self.session.get(url) #, stream=stream)

        if response.status_code == 200:
            if stream:  # for when we proxy the whole body
                response.raise_for_status()
                return response  # return the whole response when they want to stream it because we'll want to get the response headers
            else:  # for when you want to download tiles only
                if len(response.content) < 1000:
                    print("Likely empty tile")
                if path is None:
                    path = os.path.join(self.default_folder, filename)

                os.makedirs(os.path.split(path)[0], exist_ok=True)  # make all the directories needed
                with open(path, 'wb') as output:
                    output.write(
                        response.content)  # this isn't really a good way to do this for large files, but is likely fine enough for small ones
                return path
        else:
            raise RuntimeError(
                f"Server returned alternative status code: {response.status_code}. Included body '{response.content}'")