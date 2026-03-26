"""Fixtures for functional tests requiring Docker services.

Start services:  docker compose -f docker-compose.functional.yaml up -d
Stop services:   docker compose -f docker-compose.functional.yaml down -v

Tests are skipped automatically when services are not reachable.
"""

from __future__ import annotations

import io
import os
import subprocess
import time
import zipfile
from pathlib import Path

import pytest
import requests

COMPOSE_FILE = Path(__file__).parents[2] / "docker-compose.functional.yaml"
FAKE_GCS_URL = "http://localhost:4443"
QDRANT_URL = "http://localhost:6333"
GCS_BUCKET = "thresher-test"


def _service_healthy(url: str, path: str = "/") -> bool:
    try:
        r = requests.get(f"{url}{path}", timeout=2)
        return r.status_code < 500
    except Exception:
        return False


def _wait_for_services(timeout: int = 30) -> bool:
    """Wait until both fake-gcs and qdrant are healthy."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        gcs_ok = _service_healthy(FAKE_GCS_URL, "/storage/v1/b")
        qdrant_ok = _service_healthy(QDRANT_URL, "/healthz")
        if gcs_ok and qdrant_ok:
            return True
        time.sleep(1)
    return False


def _compose(*args: str) -> int:
    return subprocess.call(
        ["docker", "compose", "-f", str(COMPOSE_FILE), *args],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


@pytest.fixture(scope="session")
def docker_services():
    """Start Docker Compose services for the test session.

    If services are already running, reuse them.  If Docker is not available
    or services fail to start, skip the entire session.
    """
    # Check if services are already running
    if _wait_for_services(timeout=3):
        yield
        return

    # Try to start services
    try:
        rc = _compose("up", "-d", "--wait", "--wait-timeout", "60")
    except FileNotFoundError:
        pytest.skip("docker compose not available")
        return

    if rc != 0:
        pytest.skip("Failed to start Docker Compose services")
        return

    if not _wait_for_services(timeout=30):
        _compose("down", "-v")
        pytest.skip("Docker services did not become healthy in time")
        return

    yield

    # Teardown — leave containers running for re-use during development.
    # CI can do: docker compose -f docker-compose.functional.yaml down -v


@pytest.fixture(scope="session")
def gcs_client(docker_services):
    """Return a google.cloud.storage Client pointing at fake-gcs-server."""
    os.environ["STORAGE_EMULATOR_HOST"] = FAKE_GCS_URL
    from google.cloud import storage

    client = storage.Client()
    return client


@pytest.fixture(scope="session")
def gcs_bucket(gcs_client):
    """Ensure the test bucket exists and return it."""
    bucket = gcs_client.bucket(GCS_BUCKET)
    if not bucket.exists():
        gcs_client.create_bucket(GCS_BUCKET)
        bucket = gcs_client.bucket(GCS_BUCKET)
    return bucket


@pytest.fixture
def clean_bucket(gcs_bucket):
    """Delete all blobs in the test bucket before each test."""
    for blob in gcs_bucket.list_blobs():
        blob.delete()
    return gcs_bucket


@pytest.fixture(scope="session")
def qdrant_client(docker_services):
    """Return a qdrant_client.QdrantClient pointing at the test Qdrant."""
    from qdrant_client import QdrantClient

    client = QdrantClient(url=QDRANT_URL, timeout=30)
    return client


@pytest.fixture
def clean_qdrant(qdrant_client):
    """Delete all collections before each test."""
    for col in qdrant_client.get_collections().collections:
        qdrant_client.delete_collection(col.name)
    return qdrant_client


# ---------------------------------------------------------------------------
# Sample file helpers
# ---------------------------------------------------------------------------


def make_text_file(content: str = "Hello, Thresher!\nThis is a test document.\n") -> bytes:
    return content.encode("utf-8")


def make_source_file(
    content: str = "def hello():\n    print('Hello from thresher test')\n\nhello()\n",
) -> bytes:
    return content.encode("utf-8")


def make_zip_archive(files: dict[str, bytes] | None = None) -> bytes:
    if files is None:
        files = {
            "readme.txt": b"Test archive readme",
            "data/notes.txt": b"Nested text file in archive",
        }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


@pytest.fixture
def sample_text() -> bytes:
    return make_text_file()


@pytest.fixture
def sample_source() -> bytes:
    return make_source_file()


@pytest.fixture
def sample_zip() -> bytes:
    return make_zip_archive()
