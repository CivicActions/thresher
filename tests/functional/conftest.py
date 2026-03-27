"""Fixtures for functional tests requiring Docker services.

Start services:  docker compose -f docker-compose.functional.yaml up -d
Stop services:   docker compose -f docker-compose.functional.yaml down -v

Tests are skipped automatically when services are not reachable.
"""

from __future__ import annotations

import io
import os
import subprocess
import tempfile
import time
import warnings
import zipfile
from pathlib import Path

import pytest
import requests
import yaml

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


# ---------------------------------------------------------------------------
# K8s / k3s fixtures
# ---------------------------------------------------------------------------

K3S_CONTAINER = "thresher-k3s"
K3S_IMAGE = "rancher/k3s:v1.32.3-k3s1"
K3S_PORT = 6443


def _k3s_running() -> bool:
    """Check if our k3s container is running."""
    result = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Running}}", K3S_CONTAINER],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0 and result.stdout.strip() == "true"


def _start_k3s() -> bool:
    """Start a k3s container if not already running."""
    if _k3s_running():
        return True
    subprocess.run(["docker", "rm", "-f", K3S_CONTAINER], capture_output=True)
    result = subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--name",
            K3S_CONTAINER,
            "--privileged",
            "-p",
            f"{K3S_PORT}:6443",
            K3S_IMAGE,
            "server",
            "--disable=traefik",
            "--disable=servicelb",
            "--tls-san=localhost",
        ],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def _wait_for_k8s_api(kubeconfig: str, timeout: int = 60) -> bool:
    """Wait until the K8s API server is reachable."""
    deadline = time.monotonic() + timeout
    env = {**os.environ, "KUBECONFIG": kubeconfig}
    while time.monotonic() < deadline:
        result = subprocess.run(
            ["kubectl", "get", "ns", "default"],
            capture_output=True,
            env=env,
        )
        if result.returncode == 0:
            return True
        time.sleep(2)
    return False


def _extract_kubeconfig() -> str | None:
    """Extract kubeconfig from k3s container, fix server address and TLS."""
    result = subprocess.run(
        ["docker", "exec", K3S_CONTAINER, "cat", "/etc/rancher/k3s/k3s.yaml"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    kc = yaml.safe_load(result.stdout)
    for cluster in kc.get("clusters", []):
        c = cluster.get("cluster", {})
        c["server"] = f"https://localhost:{K3S_PORT}"
        c.pop("certificate-authority-data", None)
        c["insecure-skip-tls-verify"] = True
    path = os.path.join(tempfile.gettempdir(), "thresher-k3s-kubeconfig.yaml")
    with open(path, "w") as f:
        yaml.dump(kc, f, default_flow_style=False)
    return path


@pytest.fixture(scope="session")
def k8s_cluster():
    """Ensure a K8s cluster is available and return the kubeconfig path."""
    # The Python kubernetes client's urllib3 pool manager routes all HTTPS traffic
    # through HTTPS_PROXY (including localhost), and proxy servers refuse to forward
    # to local addresses (e.g. Docker sandbox proxy on host.docker.internal:3128).
    # Temporarily unset proxy vars so the k8s client connects directly to localhost,
    # then restore them so later tests (e.g. model downloads) can still use the proxy.
    _proxy_vars = ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy")
    _saved_proxy = {v: os.environ.pop(v) for v in _proxy_vars if v in os.environ}

    existing = os.environ.get("KUBECONFIG")
    if existing and os.path.isfile(existing) and _wait_for_k8s_api(existing, timeout=5):
        yield existing
        os.environ.update(_saved_proxy)
        return

    try:
        if not _start_k3s():
            os.environ.update(_saved_proxy)
            pytest.skip("Failed to start k3s container")
            return
    except FileNotFoundError:
        os.environ.update(_saved_proxy)
        pytest.skip("docker not available")
        return

    time.sleep(10)
    kubeconfig = _extract_kubeconfig()
    if kubeconfig is None:
        os.environ.update(_saved_proxy)
        pytest.skip("Failed to extract kubeconfig from k3s")
        return
    if not _wait_for_k8s_api(kubeconfig, timeout=60):
        os.environ.update(_saved_proxy)
        pytest.skip("K8s API did not become ready in time")
        return
    os.environ["KUBECONFIG"] = kubeconfig
    yield kubeconfig
    os.environ.update(_saved_proxy)


def _k8s_config_local(kubeconfig: str):
    """Load kubeconfig and configure the client for a local test cluster."""
    from kubernetes import client, config

    config.load_kube_config(config_file=kubeconfig)
    # k3s uses self-signed certs; skip verification for test clusters.
    cfg = client.Configuration.get_default_copy()
    cfg.verify_ssl = False
    client.Configuration.set_default(cfg)
    # Suppress urllib3 InsecureRequestWarning for all k8s fixtures.
    warnings.filterwarnings("ignore", message="Unverified HTTPS request")


@pytest.fixture(scope="session")
def k8s_api(k8s_cluster):
    """Return a kubernetes BatchV1Api client configured for the test cluster."""
    from kubernetes import client

    _k8s_config_local(k8s_cluster)
    return client.BatchV1Api()


@pytest.fixture(scope="session")
def k8s_core_api(k8s_cluster):
    """Return a kubernetes CoreV1Api client."""
    from kubernetes import client

    _k8s_config_local(k8s_cluster)
    return client.CoreV1Api()


@pytest.fixture
def clean_k8s_jobs(k8s_api):
    """Delete all thresher Jobs before each test."""
    try:
        jobs = k8s_api.list_namespaced_job(namespace="default", label_selector="app=thresher")
        for job in jobs.items:
            k8s_api.delete_namespaced_job(
                name=job.metadata.name,
                namespace="default",
                body={"propagationPolicy": "Foreground"},
            )
        # Wait until jobs are actually gone
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            remaining = k8s_api.list_namespaced_job(
                namespace="default", label_selector="app=thresher"
            )
            if not remaining.items:
                break
            time.sleep(0.5)
    except Exception:
        pass
    return k8s_api
