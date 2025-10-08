"""
Do not use as-is - I asked an LLM to take a first crack at the code, and for every good idea
it gave me about how to handle the testing, it totally messed up the rest.

This will need a by-hand rewrite, IMO, but I might do some of it in line with the strategy here, just
not the actual code.
"""


import base64
import logging
import time
from datetime import datetime, UTC, timedelta
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from main import app
from hexprox.key_manager import APIKeyManager
from hexprox.hexagon import HexagonManager, TOKEN_URL, STREAMING_WMTS_URL, PARAMS

# Fixture for the FastAPI test client
@pytest.fixture
def client():
    """Create a FastAPI test client."""
    with TestClient(app) as c:
        yield c



@pytest.fixture
def mock_hexagon_manager():
    """Fixture to mock the HexagonManager class completely."""
    mock_manager_instance = MagicMock(spec=HexagonManager)
    # Configure return values for its methods
    mock_manager_instance.token = "fake-api-token"

    # Mock the class to return our controlled instance
    mock_class = patch("main.HexagonManager", return_value=mock_manager_instance)
    return mock_class

# --- Unit Tests for HexagonManager ---

def test_hexagon_manager_init_invalid_credentials():
    """Test that HexagonManager raises PermissionError for invalid-looking credentials."""
    long_string = "a" * 20
    with pytest.raises(PermissionError, match="Invalid client ID or secret"):
        HexagonManager(client_id=long_string, client_secret="secret")
    with pytest.raises(PermissionError, match="Invalid client ID or secret"):
        HexagonManager(client_id="id", client_secret="secret with spaces")

def test_get_token_success(mock_hexagon_manager):
    """Unit test for a successful token fetch."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "access_token": "fake-test-token",
        "expires_in": 3600
    }
    mock_hexagon_manager.patch("requests.get", return_value=mock_response)

    manager = mock_hexagon_manager("test_id", "test_secret")
    token_info = manager._get_token()

    assert token_info["access_token"] == "fake-test-token"
    assert "reauthorize_after" in token_info
    assert isinstance(token_info["reauthorize_after"], datetime)

def test_get_token_failure(mocker):
    """Unit test for a failed token fetch."""
    mock_response = MagicMock()
    mock_response.status_code = 401
    mock_response.content = b"Unauthorized"
    mocker.patch("requests.get", return_value=mock_response)

    manager = HexagonManager("test_id", "test_secret")
    with pytest.raises(PermissionError, match="Couldn't get access token"):
        manager._get_token()

def test_token_property_caching_and_refresh(mocker):
    """Test that the token property caches the token and refreshes it upon expiration."""
    # Mock datetime to control time
    mock_dt = MagicMock()
    # First call, time is now
    mock_dt.now.return_value = datetime(2025, 1, 1, 12, 0, 0, tz=UTC)
    mocker.patch("hexagon.datetime", mock_dt)

    # Mock the internal _get_token method
    manager = HexagonManager("test_id", "test_secret")
    manager._get_token = MagicMock(return_value={
        "access_token": "initial-token",
        "expires_in": 3600,
        "reauthorize_after": datetime(2025, 1, 1, 12, 59, 0, tz=UTC) # Expires in just under an hour
    })

    # 1. First access, should call _get_token
    token1 = manager.token
    assert token1 == "initial-token"
    manager._get_token.assert_called_once()

    # 2. Second access (before expiry), should use cache and NOT call _get_token again
    mock_dt.now.return_value = datetime(2025, 1, 1, 12, 30, 0, tz=UTC)
    token2 = manager.token
    assert token2 == "initial-token"
    manager._get_token.assert_called_once() # Still called only once

    # 3. Third access (after expiry), should refresh by calling _get_token again
    manager._get_token.return_value["access_token"] = "refreshed-token" # Simulate new token
    mock_dt.now.return_value = datetime(2025, 1, 1, 13, 1, 0, tz=UTC)
    token3 = manager.token
    assert token3 == "refreshed-token"
    assert manager._get_token.call_count == 2 # Now called twice

# --- Integration Tests for FastAPI Endpoints ---


def test_request_counter_middleware(client, caplog):
    """Test that the middleware logs a custom dimension for counting requests."""
    with caplog.at_level(logging.INFO):
        # We make a request to a known-bad endpoint to isolate the middleware test
        client.get("/nonexistent-endpoint")

    assert "API request received" in caplog.text
    # Check that the structured log has the correct custom dimension
    request_log_record = next(rec for rec in caplog.records if rec.message == "API request received")
    assert request_log_record.custom_dimensions == {"RequestCount": 1}


def test_get_tile_v2_redirect_for_desktop_client(client, mock_hexagon_manager):
    """Test that a v2 tile request WITHOUT an Origin header returns a 307 redirect."""
    mock_class, mock_instance = mock_hexagon_manager
    tile_url = f"{STREAMING_WMTS_URL}{PARAMS}10/1/2.png&access_token=fake-api-token"
    mock_instance.get_tile.return_value = tile_url

    apikey = "4yhjakgrsdoui"

    response = client.get(f"/wmts/v2/{apikey}/10/1/2.png")

    assert response.status_code == 307
    assert response.headers["Location"] == tile_url
    # Verify manager was called correctly
    mock_instance.get_tile.assert_called_with(matrix='10', row='1', col='2', extension='png', url_only=True)


def test_get_tile_v2_proxy_for_web_client(client, mock_hexagon_manager):
    """Test that a v2 tile request WITH an Origin header proxies the content."""
    mock_class, mock_instance = mock_hexagon_manager

    # Mock the response object that requests would return
    mock_tile_response = MagicMock()
    mock_tile_response.status_code = 200
    mock_tile_response.headers = {'Content-Type': 'image/png'}
    mock_tile_response.iter_content.return_value = [b"fake-", b"png-", b"data"]

    mock_instance.get_tile.return_value = mock_tile_response

    client_id_b64 = base64.b64encode(b"test_id").decode()
    client_secret_b64 = base64.b64encode(b"test_secret").decode()

    response = client.get(
        f"/wmts/v2/{client_id_b64}/{client_secret_b64}/10/1/2.png",
        headers={"Origin": "https://my-web-map.com"}
    )

    assert response.status_code == 200
    assert response.content == b"fake-png-data"
    assert response.headers["Content-Type"] == "image/png"
    # Verify manager was called to stream
    mock_instance.get_tile.assert_called_with(matrix='10', row='1', col='2', extension='png', stream=True)


def test_invalid_base64_credentials(client):
    """Test that garbled base64 in the URL returns a 403 Forbidden error."""
    # Test both v1 and v2 endpoints for this behavior
    response_v1 = client.get("/wmts/not-base64/also-not-base64/tile.png")
    assert response_v1.status_code == 403
    assert "Invalid credentials" in response_v1.text

    response_v2 = client.get("/wmts/v2/not-base64/also-not-base64/tile.png")
    assert response_v2.status_code == 403
    assert "Invalid credentials" in response_v2.text
