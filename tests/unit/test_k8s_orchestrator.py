"""Unit tests for K8s orchestrator and CLI integration."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import yaml

from thresher.cli import main
from thresher.config import Config, K8sConfig, K8sResources, K8sResourceSpec
from thresher.controller.k8s_orchestrator import K8sOrchestrator


def _make_config(**k8s_overrides) -> Config:
    """Create a Config with optional K8s overrides."""
    k8s_kwargs: dict = {
        "namespace": "",
        "service_account": "",
        "image": "",
        "image_pull_policy": "IfNotPresent",
        "runner_resources": K8sResources(
            requests=K8sResourceSpec(cpu="500m", memory="2Gi"),
            limits=K8sResourceSpec(cpu="2", memory="4Gi"),
        ),
        "max_parallelism": 10,
        "node_selector": {},
        "tolerations": [],
        "backoff_limit": 3,
        "ttl_seconds_after_finished": 3600,
    }
    k8s_kwargs.update(k8s_overrides)
    config = Config()
    config.kubernetes = K8sConfig(**k8s_kwargs)
    return config


class TestDetectImage:
    """Tests for image detection logic."""

    def test_explicit_config_image(self):
        config = _make_config(image="myregistry/thresher:v1.0")
        orch = K8sOrchestrator(config, ["batch-001"])
        assert orch.detect_image() == "myregistry/thresher:v1.0"

    def test_env_var_image(self, monkeypatch):
        monkeypatch.setenv("THRESHER_IMAGE", "envimage:latest")
        config = _make_config(image="")
        orch = K8sOrchestrator(config, ["batch-001"])
        assert orch.detect_image() == "envimage:latest"

    def test_default_image(self, monkeypatch):
        monkeypatch.delenv("THRESHER_IMAGE", raising=False)
        config = _make_config(image="")
        orch = K8sOrchestrator(config, ["batch-001"])
        assert orch.detect_image() == "thresher:latest"

    def test_config_takes_priority_over_env(self, monkeypatch):
        monkeypatch.setenv("THRESHER_IMAGE", "envimage:latest")
        config = _make_config(image="configimage:v2")
        orch = K8sOrchestrator(config, ["batch-001"])
        assert orch.detect_image() == "configimage:v2"


class TestDetectNamespace:
    """Tests for namespace detection logic."""

    def test_explicit_config_namespace(self):
        config = _make_config(namespace="prod")
        orch = K8sOrchestrator(config, ["batch-001"])
        assert orch.detect_namespace() == "prod"

    def test_default_namespace(self):
        config = _make_config(namespace="")
        orch = K8sOrchestrator(config, ["batch-001"])
        assert orch.detect_namespace() == "default"

    def test_service_account_namespace(self, monkeypatch):
        config = _make_config(namespace="")
        orch = K8sOrchestrator(config, ["batch-001"])

        mock_path = MagicMock()
        mock_path.exists.return_value = True
        mock_path.read_text.return_value = "kube-ns\n"
        monkeypatch.setattr(
            "thresher.controller.k8s_orchestrator.Path",
            lambda *a: mock_path,
        )
        assert orch.detect_namespace() == "kube-ns"


class TestBuildJobSpecs:
    """Tests for Job spec generation."""

    def test_generates_correct_structure(self, monkeypatch):
        monkeypatch.delenv("THRESHER_IMAGE", raising=False)
        monkeypatch.delenv("GCS_BUCKET", raising=False)
        monkeypatch.delenv("QDRANT_URL", raising=False)
        monkeypatch.delenv("QDRANT_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)

        config = _make_config(image="test:v1", namespace="test-ns")
        orch = K8sOrchestrator(config, ["batch-001", "batch-002"])
        specs = orch.build_job_specs()

        assert len(specs) == 2
        spec = specs[0]
        assert spec["apiVersion"] == "batch/v1"
        assert spec["kind"] == "Job"
        assert spec["metadata"]["name"] == "thresher-runner-batch-001"
        assert spec["metadata"]["namespace"] == "test-ns"
        assert spec["metadata"]["labels"]["app"] == "thresher"
        assert spec["metadata"]["labels"]["component"] == "runner"
        assert spec["metadata"]["labels"]["batch-id"] == "batch-001"

    def test_respects_resource_limits(self, monkeypatch):
        monkeypatch.delenv("THRESHER_IMAGE", raising=False)
        monkeypatch.delenv("GCS_BUCKET", raising=False)
        monkeypatch.delenv("QDRANT_URL", raising=False)
        monkeypatch.delenv("QDRANT_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)

        config = _make_config(
            image="test:v1",
            namespace="ns",
            runner_resources=K8sResources(
                requests=K8sResourceSpec(cpu="1", memory="4Gi"),
                limits=K8sResourceSpec(cpu="4", memory="8Gi"),
            ),
        )
        orch = K8sOrchestrator(config, ["batch-001"])
        specs = orch.build_job_specs()

        container = specs[0]["spec"]["template"]["spec"]["containers"][0]
        assert container["resources"]["requests"]["cpu"] == "1"
        assert container["resources"]["requests"]["memory"] == "4Gi"
        assert container["resources"]["limits"]["cpu"] == "4"
        assert container["resources"]["limits"]["memory"] == "8Gi"

    def test_includes_env_vars(self, monkeypatch):
        monkeypatch.delenv("THRESHER_IMAGE", raising=False)
        monkeypatch.setenv("GCS_BUCKET", "my-bucket")
        monkeypatch.setenv("QDRANT_URL", "http://qdrant:6333")
        monkeypatch.delenv("QDRANT_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)

        config = _make_config(image="test:v1", namespace="ns")
        orch = K8sOrchestrator(config, ["batch-001"])
        specs = orch.build_job_specs()

        env_list = specs[0]["spec"]["template"]["spec"]["containers"][0]["env"]
        env_names = {e["name"] for e in env_list}
        assert "THRESHER_RUNNER_ID" in env_names
        assert "GCS_BUCKET" in env_names
        assert "QDRANT_URL" in env_names

        gcs_env = next(e for e in env_list if e["name"] == "GCS_BUCKET")
        assert gcs_env["value"] == "my-bucket"

    def test_includes_service_account(self, monkeypatch):
        monkeypatch.delenv("THRESHER_IMAGE", raising=False)
        monkeypatch.delenv("GCS_BUCKET", raising=False)
        monkeypatch.delenv("QDRANT_URL", raising=False)
        monkeypatch.delenv("QDRANT_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)

        config = _make_config(
            image="test:v1",
            namespace="ns",
            service_account="thresher-sa",
        )
        orch = K8sOrchestrator(config, ["batch-001"])
        specs = orch.build_job_specs()

        pod_spec = specs[0]["spec"]["template"]["spec"]
        assert pod_spec["serviceAccountName"] == "thresher-sa"

    def test_includes_node_selector_and_tolerations(self, monkeypatch):
        monkeypatch.delenv("THRESHER_IMAGE", raising=False)
        monkeypatch.delenv("GCS_BUCKET", raising=False)
        monkeypatch.delenv("QDRANT_URL", raising=False)
        monkeypatch.delenv("QDRANT_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)

        tolerations = [{"key": "gpu", "operator": "Exists", "effect": "NoSchedule"}]
        config = _make_config(
            image="test:v1",
            namespace="ns",
            node_selector={"gpu": "true"},
            tolerations=tolerations,
        )
        orch = K8sOrchestrator(config, ["batch-001"])
        specs = orch.build_job_specs()

        pod_spec = specs[0]["spec"]["template"]["spec"]
        assert pod_spec["nodeSelector"] == {"gpu": "true"}
        assert pod_spec["tolerations"] == tolerations

    def test_backoff_and_ttl(self, monkeypatch):
        monkeypatch.delenv("THRESHER_IMAGE", raising=False)
        monkeypatch.delenv("GCS_BUCKET", raising=False)
        monkeypatch.delenv("QDRANT_URL", raising=False)
        monkeypatch.delenv("QDRANT_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)

        config = _make_config(
            image="test:v1",
            namespace="ns",
            backoff_limit=5,
            ttl_seconds_after_finished=7200,
        )
        orch = K8sOrchestrator(config, ["batch-001"])
        specs = orch.build_job_specs()

        job_spec = specs[0]["spec"]
        assert job_spec["backoffLimit"] == 5
        assert job_spec["ttlSecondsAfterFinished"] == 7200

    def test_runner_args(self, monkeypatch):
        monkeypatch.delenv("THRESHER_IMAGE", raising=False)
        monkeypatch.delenv("GCS_BUCKET", raising=False)
        monkeypatch.delenv("QDRANT_URL", raising=False)
        monkeypatch.delenv("QDRANT_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)

        config = _make_config(image="test:v1", namespace="ns")
        orch = K8sOrchestrator(config, ["batch-001"])
        specs = orch.build_job_specs()

        container = specs[0]["spec"]["template"]["spec"]["containers"][0]
        assert container["args"] == ["runner", "--runner-id", "runner-batch-001"]

    def test_empty_batches(self, monkeypatch):
        monkeypatch.delenv("THRESHER_IMAGE", raising=False)
        config = _make_config(image="test:v1", namespace="ns")
        orch = K8sOrchestrator(config, [])
        specs = orch.build_job_specs()
        assert specs == []


class TestExportManifests:
    """Tests for YAML manifest export."""

    def test_writes_valid_yaml(self, monkeypatch, tmp_path):
        monkeypatch.delenv("THRESHER_IMAGE", raising=False)
        monkeypatch.delenv("GCS_BUCKET", raising=False)
        monkeypatch.delenv("QDRANT_URL", raising=False)
        monkeypatch.delenv("QDRANT_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)

        config = _make_config(image="test:v1", namespace="test-ns")
        orch = K8sOrchestrator(config, ["batch-001", "batch-002"])

        out_file = tmp_path / "jobs.yaml"
        orch.export_manifests(str(out_file))

        content = out_file.read_text()
        docs = list(yaml.safe_load_all(content))
        assert len(docs) == 2
        assert docs[0]["metadata"]["name"] == "thresher-runner-batch-001"
        assert docs[1]["metadata"]["name"] == "thresher-runner-batch-002"

    def test_single_batch_no_separator(self, monkeypatch, tmp_path):
        monkeypatch.delenv("THRESHER_IMAGE", raising=False)
        monkeypatch.delenv("GCS_BUCKET", raising=False)
        monkeypatch.delenv("QDRANT_URL", raising=False)
        monkeypatch.delenv("QDRANT_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)

        config = _make_config(image="test:v1", namespace="test-ns")
        orch = K8sOrchestrator(config, ["batch-001"])

        out_file = tmp_path / "jobs.yaml"
        orch.export_manifests(str(out_file))

        content = out_file.read_text()
        assert "---" not in content
        doc = yaml.safe_load(content)
        assert doc["metadata"]["name"] == "thresher-runner-batch-001"


class TestDeployJobs:
    """Tests for K8s Job deployment with mocked client."""

    def test_deploy_creates_jobs(self, monkeypatch):
        monkeypatch.delenv("THRESHER_IMAGE", raising=False)
        monkeypatch.delenv("GCS_BUCKET", raising=False)
        monkeypatch.delenv("QDRANT_URL", raising=False)
        monkeypatch.delenv("QDRANT_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)

        config = _make_config(image="test:v1", namespace="test-ns")
        orch = K8sOrchestrator(config, ["batch-001", "batch-002"])

        mock_batch_api = MagicMock()
        mock_client = MagicMock()
        mock_client.BatchV1Api.return_value = mock_batch_api

        mock_k8s_config = MagicMock()
        mock_k8s_config.ConfigException = Exception
        mock_k8s_config.load_incluster_config.side_effect = Exception("not in cluster")

        with (
            patch.dict(
                "sys.modules",
                {
                    "kubernetes": MagicMock(client=mock_client, config=mock_k8s_config),
                    "kubernetes.client": mock_client,
                    "kubernetes.config": mock_k8s_config,
                },
            ),
        ):
            created = orch.deploy_jobs()

        assert len(created) == 2
        assert mock_batch_api.create_namespaced_job.call_count == 2

    def test_deploy_respects_max_parallelism(self, monkeypatch):
        monkeypatch.delenv("THRESHER_IMAGE", raising=False)
        monkeypatch.delenv("GCS_BUCKET", raising=False)
        monkeypatch.delenv("QDRANT_URL", raising=False)
        monkeypatch.delenv("QDRANT_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)

        config = _make_config(image="test:v1", namespace="test-ns", max_parallelism=1)
        orch = K8sOrchestrator(config, ["batch-001", "batch-002", "batch-003"])

        mock_batch_api = MagicMock()
        mock_client = MagicMock()
        mock_client.BatchV1Api.return_value = mock_batch_api

        mock_k8s_config = MagicMock()
        mock_k8s_config.ConfigException = Exception
        mock_k8s_config.load_incluster_config.side_effect = Exception("not in cluster")

        with (
            patch.dict(
                "sys.modules",
                {
                    "kubernetes": MagicMock(client=mock_client, config=mock_k8s_config),
                    "kubernetes.client": mock_client,
                    "kubernetes.config": mock_k8s_config,
                },
            ),
        ):
            created = orch.deploy_jobs()

        assert len(created) == 1
        assert mock_batch_api.create_namespaced_job.call_count == 1

    def test_deploy_handles_failure(self, monkeypatch):
        monkeypatch.delenv("THRESHER_IMAGE", raising=False)
        monkeypatch.delenv("GCS_BUCKET", raising=False)
        monkeypatch.delenv("QDRANT_URL", raising=False)
        monkeypatch.delenv("QDRANT_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)

        config = _make_config(image="test:v1", namespace="test-ns")
        orch = K8sOrchestrator(config, ["batch-001"])

        mock_batch_api = MagicMock()
        mock_batch_api.create_namespaced_job.side_effect = RuntimeError("API error")
        mock_client = MagicMock()
        mock_client.BatchV1Api.return_value = mock_batch_api

        mock_k8s_config = MagicMock()
        mock_k8s_config.ConfigException = Exception
        mock_k8s_config.load_incluster_config.side_effect = Exception("not in cluster")

        with (
            patch.dict(
                "sys.modules",
                {
                    "kubernetes": MagicMock(client=mock_client, config=mock_k8s_config),
                    "kubernetes.client": mock_client,
                    "kubernetes.config": mock_k8s_config,
                },
            ),
        ):
            created = orch.deploy_jobs()

        assert len(created) == 0


class TestCLIMutualExclusivity:
    """Tests for CLI mode mutual exclusivity."""

    def test_local_and_k8s_deploy_are_exclusive(self, monkeypatch):
        """Cannot use --local and --k8s-deploy together."""

        def mock_scan(source, config):
            return [{"path": "f.txt", "source_type": "direct", "file_type_group": "text"}]

        def mock_build_queue(items, source, **kwargs):
            return ["batch-001"]

        def mock_create_source(config):
            return MagicMock()

        monkeypatch.setattr("thresher.controller.scanner.scan_files", mock_scan)
        monkeypatch.setattr("thresher.controller.queue_builder.build_queue", mock_build_queue)
        monkeypatch.setattr("thresher.runner.processor.create_source_provider", mock_create_source)

        result = main(["controller", "--local", "--k8s-deploy"])
        assert result == 1

    def test_local_and_manifest_out_are_exclusive(self, monkeypatch):
        """Cannot use --local and --k8s-manifest-out together."""

        def mock_scan(source, config):
            return [{"path": "f.txt", "source_type": "direct", "file_type_group": "text"}]

        def mock_build_queue(items, source, **kwargs):
            return ["batch-001"]

        def mock_create_source(config):
            return MagicMock()

        monkeypatch.setattr("thresher.controller.scanner.scan_files", mock_scan)
        monkeypatch.setattr("thresher.controller.queue_builder.build_queue", mock_build_queue)
        monkeypatch.setattr("thresher.runner.processor.create_source_provider", mock_create_source)

        result = main(["controller", "--local", "--k8s-manifest-out", "out.yaml"])
        assert result == 1

    def test_all_three_modes_exclusive(self, monkeypatch):
        """Cannot use all three modes together."""

        def mock_scan(source, config):
            return [{"path": "f.txt", "source_type": "direct", "file_type_group": "text"}]

        def mock_build_queue(items, source, **kwargs):
            return ["batch-001"]

        def mock_create_source(config):
            return MagicMock()

        monkeypatch.setattr("thresher.controller.scanner.scan_files", mock_scan)
        monkeypatch.setattr("thresher.controller.queue_builder.build_queue", mock_build_queue)
        monkeypatch.setattr("thresher.runner.processor.create_source_provider", mock_create_source)

        result = main(
            [
                "controller",
                "--local",
                "--k8s-deploy",
                "--k8s-manifest-out",
                "out.yaml",
            ]
        )
        assert result == 1


class TestCLIK8sManifestOut:
    """Tests for CLI --k8s-manifest-out integration."""

    def test_manifest_out_creates_file(self, monkeypatch, tmp_path):
        """--k8s-manifest-out should export manifests."""
        monkeypatch.delenv("THRESHER_IMAGE", raising=False)
        monkeypatch.delenv("GCS_BUCKET", raising=False)
        monkeypatch.delenv("QDRANT_URL", raising=False)
        monkeypatch.delenv("QDRANT_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)

        def mock_scan(source, config):
            return [{"path": "f.txt", "source_type": "direct", "file_type_group": "text"}]

        def mock_build_queue(items, source, **kwargs):
            return ["batch-001"]

        def mock_create_source(config):
            return MagicMock()

        monkeypatch.setattr("thresher.controller.scanner.scan_files", mock_scan)
        monkeypatch.setattr("thresher.controller.queue_builder.build_queue", mock_build_queue)
        monkeypatch.setattr("thresher.runner.processor.create_source_provider", mock_create_source)

        out_file = tmp_path / "manifests.yaml"
        result = main(["controller", "--k8s-manifest-out", str(out_file)])

        assert result == 0
        assert out_file.exists()
        doc = yaml.safe_load(out_file.read_text())
        assert doc["kind"] == "Job"
        assert doc["metadata"]["name"] == "thresher-runner-batch-001"

    def test_empty_queue_skips_k8s(self, monkeypatch, tmp_path):
        """No batches → no K8s action, returns 0."""

        def mock_scan(source, config):
            return []

        def mock_build_queue(items, source, **kwargs):
            return []

        def mock_create_source(config):
            return MagicMock()

        monkeypatch.setattr("thresher.controller.scanner.scan_files", mock_scan)
        monkeypatch.setattr("thresher.controller.queue_builder.build_queue", mock_build_queue)
        monkeypatch.setattr("thresher.runner.processor.create_source_provider", mock_create_source)

        out_file = tmp_path / "manifests.yaml"
        result = main(["controller", "--k8s-manifest-out", str(out_file)])

        assert result == 0
        assert not out_file.exists()
