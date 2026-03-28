"""K8s Job orchestrator for creating runner Jobs."""

from __future__ import annotations

import hashlib
import logging
import os
import re
from pathlib import Path
from typing import Any

import yaml

from thresher.config import Config

logger = logging.getLogger("thresher.controller.k8s_orchestrator")

# K8s name limit (RFC 1123 subdomain): max 63 chars, [a-z0-9] at start/end,
# only lowercase alphanumeric and '-' in between.
_MAX_K8S_NAME_LEN = 63
# K8s label value limit: max 63 chars, [a-zA-Z0-9] at start/end,
# only alphanumeric, '-', '_', '.' in between.
_MAX_K8S_LABEL_LEN = 63


def _sanitize_k8s_name(raw: str) -> str:
    """Sanitize a string into a valid RFC 1123 subdomain name for K8s."""
    name = raw.lower()
    name = re.sub(r"[^a-z0-9-]", "-", name)
    name = re.sub(r"-{2,}", "-", name)
    name = name.strip("-")
    return name[:_MAX_K8S_NAME_LEN].rstrip("-")


def _sanitize_k8s_label(raw: str) -> str:
    """Sanitize a string into a valid K8s label value."""
    label = re.sub(r"[^A-Za-z0-9._-]", "_", raw)
    label = label.strip("_.-")
    label = label[:_MAX_K8S_LABEL_LEN]
    return label.rstrip("_.-")


class K8sOrchestrator:
    """Creates and manages runner K8s Jobs."""

    def __init__(self, config: Config, batch_ids: list[str]):
        self.config = config
        self.batch_ids = batch_ids
        self.k8s = config.kubernetes

    def detect_image(self) -> str:
        """Detect the container image to use for runner Jobs.

        Priority:
        1. Explicit config (kubernetes.image)
        2. Self-referencing from pod metadata (THRESHER_IMAGE env var)
        3. Default fallback
        """
        if self.k8s.image:
            return self.k8s.image

        pod_image = os.environ.get("THRESHER_IMAGE")
        if pod_image:
            return pod_image

        return "thresher:latest"

    def detect_namespace(self) -> str:
        """Detect the K8s namespace.

        Priority:
        1. Explicit config
        2. Current namespace from service account path
        3. Default
        """
        if self.k8s.namespace:
            return self.k8s.namespace

        ns_path = Path("/var/run/secrets/kubernetes.io/serviceaccount/namespace")
        if ns_path.exists():
            return ns_path.read_text().strip()

        return "default"

    def build_job_specs(self) -> list[dict[str, Any]]:
        """Build K8s Job specs for all batches."""
        image = self.detect_image()
        namespace = self.detect_namespace()
        specs: list[dict[str, Any]] = []

        for batch_id in self.batch_ids:
            runner_id = f"runner-{batch_id}"
            job_name = f"thresher-{runner_id}"

            spec = {
                "apiVersion": "batch/v1",
                "kind": "Job",
                "metadata": {
                    "name": job_name,
                    "namespace": namespace,
                    "labels": {
                        "app": "thresher",
                        "component": "runner",
                        "batch-id": batch_id,
                    },
                },
                "spec": {
                    "backoffLimit": self.k8s.backoff_limit,
                    "ttlSecondsAfterFinished": self.k8s.ttl_seconds_after_finished,
                    "template": {
                        "metadata": {
                            "labels": {
                                "app": "thresher",
                                "component": "runner",
                                "batch-id": batch_id,
                            },
                        },
                        "spec": self._build_pod_spec(image, runner_id),
                    },
                },
            }
            specs.append(spec)

        return specs

    def _build_pod_spec(self, image: str, runner_id: str) -> dict[str, Any]:
        """Build the pod spec for a runner Job."""
        args = ["runner", "--runner-id", runner_id]

        # If a config ConfigMap is set, pass -c to the runner
        if self.k8s.config_configmap:
            args = ["-c", "/config/config.yaml"] + args

        container: dict[str, Any] = {
            "name": "runner",
            "image": image,
            "imagePullPolicy": self.k8s.image_pull_policy,
            "args": args,
            "resources": {
                "requests": {},
                "limits": {},
            },
            "env": [
                {"name": "THRESHER_RUNNER_ID", "value": runner_id},
            ],
            "volumeMounts": [],
        }

        if self.k8s.runner_resources.requests.cpu:
            container["resources"]["requests"]["cpu"] = self.k8s.runner_resources.requests.cpu
        if self.k8s.runner_resources.requests.memory:
            container["resources"]["requests"]["memory"] = self.k8s.runner_resources.requests.memory
        if self.k8s.runner_resources.limits.cpu:
            container["resources"]["limits"]["cpu"] = self.k8s.runner_resources.limits.cpu
        if self.k8s.runner_resources.limits.memory:
            container["resources"]["limits"]["memory"] = self.k8s.runner_resources.limits.memory

        # Propagate environment variables for cloud services
        for env_var in (
            "GCS_BUCKET",
            "QDRANT_URL",
            "QDRANT_API_KEY",
        ):
            val = os.environ.get(env_var)
            if val:
                container["env"].append({"name": env_var, "value": val})

        volumes: list[dict[str, Any]] = []

        # Mount config ConfigMap
        if self.k8s.config_configmap:
            volumes.append(
                {
                    "name": "config",
                    "configMap": {"name": self.k8s.config_configmap},
                }
            )
            container["volumeMounts"].append(
                {"name": "config", "mountPath": "/config", "readOnly": True}
            )

        # Mount GCS credentials Secret
        if self.k8s.credentials_secret:
            volumes.append(
                {
                    "name": "gcs-credentials",
                    "secret": {"secretName": self.k8s.credentials_secret},
                }
            )
            container["volumeMounts"].append(
                {"name": "gcs-credentials", "mountPath": "/secrets/gcs", "readOnly": True}
            )
            container["env"].append(
                {"name": "GOOGLE_APPLICATION_CREDENTIALS", "value": "/secrets/gcs/key.json"}
            )

        # Clean up empty volumeMounts
        if not container["volumeMounts"]:
            del container["volumeMounts"]

        pod_spec: dict[str, Any] = {
            "containers": [container],
            "restartPolicy": "Never",
        }

        if volumes:
            pod_spec["volumes"] = volumes

        if self.k8s.service_account:
            pod_spec["serviceAccountName"] = self.k8s.service_account

        if self.k8s.node_selector:
            pod_spec["nodeSelector"] = self.k8s.node_selector

        if self.k8s.tolerations:
            pod_spec["tolerations"] = self.k8s.tolerations

        return pod_spec

    def build_expansion_job_specs(self, archive_paths: list[str]) -> list[dict[str, Any]]:
        """Build K8s Job specs for archive expansion (one per archive)."""
        image = self.detect_image()
        namespace = self.detect_namespace()
        specs: list[dict[str, Any]] = []

        for archive_path in archive_paths:
            stem = archive_path.rsplit("/", 1)[-1].split(".")[0]
            path_hash = hashlib.sha256(archive_path.encode()).hexdigest()[:8]
            job_name = _sanitize_k8s_name(f"thresher-expander-{stem}-{path_hash}")

            args = ["expander", "--archive-path", archive_path]
            if self.k8s.config_configmap:
                args = ["-c", "/config/config.yaml"] + args

            container: dict[str, Any] = {
                "name": "expander",
                "image": image,
                "imagePullPolicy": self.k8s.image_pull_policy,
                "args": args,
                "resources": {
                    "requests": {},
                    "limits": {},
                },
                "env": [],
                "volumeMounts": [],
            }

            if self.k8s.expander_resources.requests.cpu:
                container["resources"]["requests"]["cpu"] = self.k8s.expander_resources.requests.cpu
            if self.k8s.expander_resources.requests.memory:
                container["resources"]["requests"]["memory"] = (
                    self.k8s.expander_resources.requests.memory
                )
            if self.k8s.expander_resources.limits.cpu:
                container["resources"]["limits"]["cpu"] = self.k8s.expander_resources.limits.cpu
            if self.k8s.expander_resources.limits.memory:
                container["resources"]["limits"]["memory"] = (
                    self.k8s.expander_resources.limits.memory
                )

            for env_var in ("GCS_BUCKET", "QDRANT_URL", "QDRANT_API_KEY"):
                val = os.environ.get(env_var)
                if val:
                    container["env"].append({"name": env_var, "value": val})

            volumes: list[dict[str, Any]] = []
            if self.k8s.config_configmap:
                volumes.append({"name": "config", "configMap": {"name": self.k8s.config_configmap}})
                container["volumeMounts"].append(
                    {"name": "config", "mountPath": "/config", "readOnly": True}
                )
            if self.k8s.credentials_secret:
                volumes.append(
                    {
                        "name": "gcs-credentials",
                        "secret": {"secretName": self.k8s.credentials_secret},
                    }
                )
                container["volumeMounts"].append(
                    {"name": "gcs-credentials", "mountPath": "/secrets/gcs", "readOnly": True}
                )
                container["env"].append(
                    {"name": "GOOGLE_APPLICATION_CREDENTIALS", "value": "/secrets/gcs/key.json"}
                )

            if not container["volumeMounts"]:
                del container["volumeMounts"]
            if not container["env"]:
                del container["env"]

            pod_spec: dict[str, Any] = {
                "containers": [container],
                "restartPolicy": "Never",
            }
            if volumes:
                pod_spec["volumes"] = volumes
            if self.k8s.service_account:
                pod_spec["serviceAccountName"] = self.k8s.service_account
            if self.k8s.node_selector:
                pod_spec["nodeSelector"] = self.k8s.node_selector
            if self.k8s.tolerations:
                pod_spec["tolerations"] = self.k8s.tolerations

            spec = {
                "apiVersion": "batch/v1",
                "kind": "Job",
                "metadata": {
                    "name": job_name,
                    "namespace": namespace,
                    "labels": {
                        "app": "thresher",
                        "component": "expander",
                        "archive-path": _sanitize_k8s_label(archive_path),
                    },
                },
                "spec": {
                    "backoffLimit": 1,
                    "ttlSecondsAfterFinished": self.k8s.ttl_seconds_after_finished,
                    "template": {
                        "metadata": {
                            "labels": {
                                "app": "thresher",
                                "component": "expander",
                            },
                        },
                        "spec": pod_spec,
                    },
                },
            }
            specs.append(spec)

        return specs

    def deploy_jobs(self) -> list[str]:
        """Deploy runner Jobs to K8s cluster. Returns list of created job names."""
        try:
            from kubernetes import client
            from kubernetes import config as k8s_config
        except ImportError:
            raise RuntimeError(
                "kubernetes package required for --k8s-deploy. Install with: pip install kubernetes"
            )

        try:
            k8s_config.load_incluster_config()
        except k8s_config.ConfigException:
            k8s_config.load_kube_config()

        batch_api = client.BatchV1Api()
        specs = self.build_job_specs()
        created: list[str] = []

        max_jobs = min(len(specs), self.k8s.max_parallelism)

        for spec in specs[:max_jobs]:
            job_name = spec["metadata"]["name"]
            namespace = spec["metadata"]["namespace"]
            try:
                batch_api.create_namespaced_job(namespace=namespace, body=spec)
                created.append(job_name)
                logger.info("Created K8s Job: %s", job_name)
            except Exception as e:
                logger.error("Failed to create Job %s: %s", job_name, e)

        return created

    def export_manifests(self, output_path: str) -> None:
        """Export Job specs as YAML manifests to a file."""
        specs = self.build_job_specs()

        with open(output_path, "w") as f:
            for i, spec in enumerate(specs):
                if i > 0:
                    f.write("---\n")
                yaml.dump(spec, f, default_flow_style=False, sort_keys=False)

        logger.info("Exported %d Job manifests to %s", len(specs), output_path)
