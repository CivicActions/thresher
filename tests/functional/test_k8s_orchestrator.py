"""Functional tests for K8sOrchestrator against a real K8s cluster.

Requires a running K8s API server (k3s container, kind, or other).
Jobs are created with real K8s API calls. Pods may not run to completion
in constrained environments (no image pull), but Job creation, metadata,
parallelism, and manifest workflows are fully tested.
"""

from __future__ import annotations

import os

import pytest

from thresher.config import (
    Config,
    K8sConfig,
    K8sResources,
    K8sResourceSpec,
)

pytestmark = pytest.mark.functional


def _make_config(
    image: str = "busybox:latest",
    namespace: str = "default",
    max_parallelism: int = 10,
    backoff_limit: int = 0,
    ttl: int = 300,
    cpu_req: str = "50m",
    mem_req: str = "32Mi",
    cpu_lim: str = "100m",
    mem_lim: str = "64Mi",
) -> Config:
    """Build a minimal Config with K8s settings for testing."""
    return Config(
        kubernetes=K8sConfig(
            namespace=namespace,
            image=image,
            image_pull_policy="IfNotPresent",
            runner_resources=K8sResources(
                requests=K8sResourceSpec(cpu=cpu_req, memory=mem_req),
                limits=K8sResourceSpec(cpu=cpu_lim, memory=mem_lim),
            ),
            max_parallelism=max_parallelism,
            backoff_limit=backoff_limit,
            ttl_seconds_after_finished=ttl,
        ),
    )


class TestJobCreation:
    """Test that K8sOrchestrator.deploy_jobs() creates Jobs in a real cluster."""

    def test_creates_single_job(self, clean_k8s_jobs):
        from thresher.controller.k8s_orchestrator import K8sOrchestrator

        config = _make_config()
        orchestrator = K8sOrchestrator(config, batch_ids=["batch-001"])
        created = orchestrator.deploy_jobs()

        assert len(created) == 1
        assert created[0] == "thresher-runner-batch-001"

        # Verify via K8s API
        job = clean_k8s_jobs.read_namespaced_job(
            name="thresher-runner-batch-001", namespace="default"
        )
        assert job.metadata.name == "thresher-runner-batch-001"
        assert job.metadata.labels["app"] == "thresher"
        assert job.metadata.labels["component"] == "runner"
        assert job.metadata.labels["batch-id"] == "batch-001"

    def test_creates_multiple_jobs(self, clean_k8s_jobs):
        from thresher.controller.k8s_orchestrator import K8sOrchestrator

        config = _make_config()
        orchestrator = K8sOrchestrator(config, batch_ids=["batch-a", "batch-b", "batch-c"])
        created = orchestrator.deploy_jobs()

        assert len(created) == 3
        # Verify all exist
        jobs = clean_k8s_jobs.list_namespaced_job(
            namespace="default", label_selector="app=thresher"
        )
        names = {j.metadata.name for j in jobs.items}
        assert names == {
            "thresher-runner-batch-a",
            "thresher-runner-batch-b",
            "thresher-runner-batch-c",
        }

    def test_job_spec_correct(self, clean_k8s_jobs):
        from thresher.controller.k8s_orchestrator import K8sOrchestrator

        config = _make_config(
            image="busybox:1.36",
            backoff_limit=2,
            ttl=600,
            cpu_req="200m",
            mem_req="128Mi",
            cpu_lim="500m",
            mem_lim="256Mi",
        )
        orchestrator = K8sOrchestrator(config, batch_ids=["spec-test"])
        orchestrator.deploy_jobs()

        job = clean_k8s_jobs.read_namespaced_job(
            name="thresher-runner-spec-test", namespace="default"
        )
        assert job.spec.backoff_limit == 2
        assert job.spec.ttl_seconds_after_finished == 600

        container = job.spec.template.spec.containers[0]
        assert container.name == "runner"
        assert container.image == "busybox:1.36"
        assert container.image_pull_policy == "IfNotPresent"
        assert container.args == ["runner", "--runner-id", "runner-spec-test"]

        # Resource limits
        assert container.resources.requests["cpu"] == "200m"
        assert container.resources.requests["memory"] == "128Mi"
        assert container.resources.limits["cpu"] == "500m"
        assert container.resources.limits["memory"] == "256Mi"

    def test_restart_policy_on_failure(self, clean_k8s_jobs):
        from thresher.controller.k8s_orchestrator import K8sOrchestrator

        config = _make_config()
        orchestrator = K8sOrchestrator(config, batch_ids=["restart-test"])
        orchestrator.deploy_jobs()

        job = clean_k8s_jobs.read_namespaced_job(
            name="thresher-runner-restart-test", namespace="default"
        )
        assert job.spec.template.spec.restart_policy == "OnFailure"


class TestMaxParallelism:
    """Test that max_parallelism limits the number of Jobs created."""

    def test_parallelism_limits_jobs(self, clean_k8s_jobs):
        from thresher.controller.k8s_orchestrator import K8sOrchestrator

        config = _make_config(max_parallelism=2)
        batch_ids = [f"par-{i}" for i in range(5)]
        orchestrator = K8sOrchestrator(config, batch_ids=batch_ids)
        created = orchestrator.deploy_jobs()

        assert len(created) == 2

        jobs = clean_k8s_jobs.list_namespaced_job(
            namespace="default", label_selector="app=thresher"
        )
        assert len(jobs.items) == 2

    def test_parallelism_one(self, clean_k8s_jobs):
        from thresher.controller.k8s_orchestrator import K8sOrchestrator

        config = _make_config(max_parallelism=1)
        orchestrator = K8sOrchestrator(config, batch_ids=["solo-a", "solo-b", "solo-c"])
        created = orchestrator.deploy_jobs()

        assert len(created) == 1
        assert created[0] == "thresher-runner-solo-a"


class TestEnvPropagation:
    """Test that environment variables are propagated to pod specs."""

    def test_runner_id_env(self, clean_k8s_jobs):
        from thresher.controller.k8s_orchestrator import K8sOrchestrator

        config = _make_config()
        orchestrator = K8sOrchestrator(config, batch_ids=["env-test"])
        orchestrator.deploy_jobs()

        job = clean_k8s_jobs.read_namespaced_job(
            name="thresher-runner-env-test", namespace="default"
        )
        container = job.spec.template.spec.containers[0]
        env_map = {e.name: e.value for e in container.env}
        assert env_map["THRESHER_RUNNER_ID"] == "runner-env-test"

    def test_gcs_and_qdrant_env_propagated(self, clean_k8s_jobs):
        from thresher.controller.k8s_orchestrator import K8sOrchestrator

        # Set env vars that the orchestrator should propagate
        old_gcs = os.environ.get("GCS_BUCKET")
        old_qdrant = os.environ.get("QDRANT_URL")
        try:
            os.environ["GCS_BUCKET"] = "test-bucket-func"
            os.environ["QDRANT_URL"] = "http://qdrant:6333"

            config = _make_config()
            orchestrator = K8sOrchestrator(config, batch_ids=["env-prop"])
            orchestrator.deploy_jobs()

            job = clean_k8s_jobs.read_namespaced_job(
                name="thresher-runner-env-prop", namespace="default"
            )
            container = job.spec.template.spec.containers[0]
            env_map = {e.name: e.value for e in container.env}

            assert env_map["GCS_BUCKET"] == "test-bucket-func"
            assert env_map["QDRANT_URL"] == "http://qdrant:6333"
        finally:
            # Restore
            if old_gcs is None:
                os.environ.pop("GCS_BUCKET", None)
            else:
                os.environ["GCS_BUCKET"] = old_gcs
            if old_qdrant is None:
                os.environ.pop("QDRANT_URL", None)
            else:
                os.environ["QDRANT_URL"] = old_qdrant

    def test_missing_env_not_propagated(self, clean_k8s_jobs):
        from thresher.controller.k8s_orchestrator import K8sOrchestrator

        # Ensure these env vars are NOT set
        old_creds = os.environ.pop("GOOGLE_APPLICATION_CREDENTIALS", None)
        try:
            config = _make_config()
            orchestrator = K8sOrchestrator(config, batch_ids=["env-miss"])
            orchestrator.deploy_jobs()

            job = clean_k8s_jobs.read_namespaced_job(
                name="thresher-runner-env-miss", namespace="default"
            )
            container = job.spec.template.spec.containers[0]
            env_names = {e.name for e in container.env}
            assert "GOOGLE_APPLICATION_CREDENTIALS" not in env_names
        finally:
            if old_creds is not None:
                os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = old_creds


class TestManifestApply:
    """Test the manifest export → apply workflow against real K8s API."""

    def test_export_and_apply(self, clean_k8s_jobs, tmp_path):
        import yaml

        from thresher.controller.k8s_orchestrator import K8sOrchestrator

        config = _make_config()
        orchestrator = K8sOrchestrator(config, batch_ids=["manifest-a", "manifest-b"])

        # Export to file
        manifest_path = str(tmp_path / "jobs.yaml")
        orchestrator.export_manifests(manifest_path)

        # Read and parse the manifests
        with open(manifest_path) as f:
            docs = list(yaml.safe_load_all(f))

        assert len(docs) == 2
        assert docs[0]["metadata"]["name"] == "thresher-runner-manifest-a"
        assert docs[1]["metadata"]["name"] == "thresher-runner-manifest-b"

        # Apply via K8s API (not kubectl)
        for doc in docs:
            clean_k8s_jobs.create_namespaced_job(namespace=doc["metadata"]["namespace"], body=doc)

        # Verify
        jobs = clean_k8s_jobs.list_namespaced_job(
            namespace="default", label_selector="app=thresher"
        )
        names = {j.metadata.name for j in jobs.items}
        assert "thresher-runner-manifest-a" in names
        assert "thresher-runner-manifest-b" in names

    def test_export_contains_valid_k8s_spec(self, tmp_path):
        import yaml

        from thresher.controller.k8s_orchestrator import K8sOrchestrator

        config = _make_config()
        orchestrator = K8sOrchestrator(config, batch_ids=["valid-spec"])

        manifest_path = str(tmp_path / "job.yaml")
        orchestrator.export_manifests(manifest_path)

        with open(manifest_path) as f:
            doc = yaml.safe_load(f)

        assert doc["apiVersion"] == "batch/v1"
        assert doc["kind"] == "Job"
        assert "template" in doc["spec"]
        assert "containers" in doc["spec"]["template"]["spec"]


class TestJobLifecycle:
    """Test Job lifecycle in the cluster.

    Note: In constrained Docker environments (no outbound internet), pods
    may not pull images. These tests verify the Job object lifecycle rather
    than pod completion.
    """

    def test_job_appears_in_listing(self, clean_k8s_jobs):
        from thresher.controller.k8s_orchestrator import K8sOrchestrator

        config = _make_config()
        orchestrator = K8sOrchestrator(config, batch_ids=["lifecycle-01"])
        orchestrator.deploy_jobs()

        jobs = clean_k8s_jobs.list_namespaced_job(
            namespace="default", label_selector="batch-id=lifecycle-01"
        )
        assert len(jobs.items) == 1
        assert jobs.items[0].status is not None

    def test_job_has_pod_template_labels(self, clean_k8s_jobs):
        from thresher.controller.k8s_orchestrator import K8sOrchestrator

        config = _make_config()
        orchestrator = K8sOrchestrator(config, batch_ids=["label-test"])
        orchestrator.deploy_jobs()

        job = clean_k8s_jobs.read_namespaced_job(
            name="thresher-runner-label-test", namespace="default"
        )
        pod_labels = job.spec.template.metadata.labels
        assert pod_labels["app"] == "thresher"
        assert pod_labels["component"] == "runner"
        assert pod_labels["batch-id"] == "label-test"

    def test_duplicate_job_fails_gracefully(self, clean_k8s_jobs):
        """Creating a Job with the same name should be handled."""
        from thresher.controller.k8s_orchestrator import K8sOrchestrator

        config = _make_config()

        # Create first
        o1 = K8sOrchestrator(config, batch_ids=["dup-test"])
        created1 = o1.deploy_jobs()
        assert len(created1) == 1

        # Create duplicate — the orchestrator logs errors but doesn't crash
        o2 = K8sOrchestrator(config, batch_ids=["dup-test"])
        created2 = o2.deploy_jobs()
        # deploy_jobs catches exceptions per-job, so it returns empty list
        assert len(created2) == 0
