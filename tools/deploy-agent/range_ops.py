"""Server 3 lifecycle operations shared by make-cells.py and reconcile.py."""
import base64
import hashlib
import json
import logging
import os
import pathlib
import re
import shlex
import subprocess
import sys
import tempfile
import time

import agent

log = logging.getLogger("jengasec-range")

DIGEST_RE = re.compile(r"digest:\s*(sha256:[0-9a-f]{64})", re.IGNORECASE)
NAMESPACE_RE = re.compile(r"[a-z0-9](?:[-a-z0-9]{0,61}[a-z0-9])?")


def require_namespace(value):
    if not isinstance(value, str) or not NAMESPACE_RE.fullmatch(value):
        raise agent.Stop(f"Invalid Kubernetes namespace from platform: {value!r}")
    return value


def api_client(base=None, token=None):
    base = base or os.environ.get("JENGASEC_BASE", agent.DEFAULT_BASE)
    token = token or os.environ.get("JENGASEC_DEPLOY_TOKEN", "")
    if not token:
        raise agent.Stop("Set JENGASEC_DEPLOY_TOKEN to the deployment API secret.")
    return agent.Api(base, token)


def api_get(api, path):
    return api._call(path)


def api_post(api, path, body):
    return api._call(path, body)


def registry_prefix(value=None):
    value = value or os.environ.get("JENGASEC_REGISTRY", "")
    value = value.rstrip("/") if isinstance(value, str) else value
    if (
        not value
        or "://" in value
        or "@" in value
        or any(ch.isspace() for ch in value)
        or not re.fullmatch(r"[A-Za-z0-9.-]+(?::\d+)?(?:/[a-z0-9._-]+)*", value)
    ):
        raise agent.Stop(
            "Set JENGASEC_REGISTRY to the private registry/repository prefix "
            "(for example registry.internal:5000/jengasec/apps), without a scheme."
        )
    return value


def docker_config_for_source(submission, image, directory, registry):
    row = {**submission, "image_reference": image}
    path = agent.docker_config(row, directory)
    username = os.environ.get("JENGASEC_REGISTRY_USERNAME", "")
    password = os.environ.get("JENGASEC_REGISTRY_PASSWORD", "")
    if bool(username) != bool(password):
        raise agent.Stop(
            "Set both JENGASEC_REGISTRY_USERNAME and JENGASEC_REGISTRY_PASSWORD, "
            "or leave both unset for an anonymous private registry."
        )
    with open(path, encoding="utf-8") as handle:
        config = json.load(handle)
    if username:
        local = registry.split("/", 1)[0]
        config.setdefault("auths", {})[local] = {
            "auth": base64.b64encode(f"{username}:{password}".encode()).decode()
        }
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(config, handle)
        os.chmod(path, 0o600)
    return path


def copy_image(submission, source, destination, config_dir):
    if not submission.get("pull_token"):
        raise agent.Stop(
            f"No usable registry token is available to copy {source} into the JengaSec registry."
        )
    config = docker_config_for_source(
        submission, source, config_dir, registry_prefix()
    )
    code, _, error = agent.docker(
        ["pull", "--platform", "linux/amd64", source], config_dir=config
    )
    if code:
        raise agent.Infra(f"Could not pull {source}: {error.strip()[:300]}")
    code, _, error = agent.docker(["tag", source, destination], config_dir=config)
    if code:
        raise agent.Infra(f"Could not tag frozen image: {error.strip()[:300]}")
    code, output, error = agent.docker(["push", destination], config_dir=config)
    if code:
        raise agent.Infra(f"Could not push image to JengaSec registry: {error.strip()[:300]}")
    matches = DIGEST_RE.findall(output + "\n" + error)
    if not matches:
        raise agent.Infra("Registry push succeeded without returning a sha256 digest.")
    return f"{destination.rsplit(':', 1)[0]}@{matches[-1].lower()}"


def dump_database(namespace):
    code, output, error = agent.kubectl(
        [
            "exec", "deployment/db", "-n", namespace, "--",
            "pg_dump", "-U", "app", "-d", "app", "--no-owner", "--no-privileges",
        ],
        timeout=300,
    )
    if code or not output.strip():
        raise agent.Infra(
            f"Could not snapshot the seed database in {namespace}: "
            f"{(error or 'empty pg_dump output').strip()[:300]}"
        )
    return output


def preview_freeze(api, competition):
    response = api_get(
        api, f"/deployments/api/freeze-candidates/?competition={int(competition)}"
    )
    candidates = response.get("candidates")
    missing = response.get("missing")
    if not isinstance(candidates, list) or not isinstance(missing, list):
        raise agent.Stop("The platform returned an invalid freeze-candidate response.")
    for candidate in candidates:
        log.info(
            "Would freeze %s: %s",
            candidate.get("cell") or candidate.get("team"),
            candidate.get("image_reference"),
        )
    for team in missing:
        log.error("NO PASSING IMAGE: %s", team.get("cell", team.get("team")))
    log.info(
        "Preview: %d image(s) ready to freeze, %d team(s) missing a passing image.",
        len(candidates),
        len(missing),
    )
    if missing:
        raise agent.Stop("Freeze preview found teams without a passing application.")


def atomic_write(path, data, mode=0o600):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def freeze(api, competition, state_dir, registry):
    prefix = registry_prefix(registry)
    response = api_get(
        api, f"/deployments/api/freeze-candidates/?competition={int(competition)}"
    )
    candidates = response.get("candidates")
    missing = response.get("missing")
    if not isinstance(candidates, list) or not isinstance(missing, list):
        raise agent.Stop("The platform returned an invalid freeze-candidate response.")
    if missing:
        for row in missing:
            log.error("NO PASSING IMAGE: %s", row.get("cell", row.get("team")))
        raise agent.Stop(
            f"{len(missing)} application team(s) have no passing submission; "
            "no images or databases were frozen."
        )

    directory = pathlib.Path(state_dir) / f"competition-{int(competition)}"
    manifest_path = directory / "freeze.json"
    try:
        with open(manifest_path, encoding="utf-8") as handle:
            manifest = json.load(handle)
    except FileNotFoundError:
        manifest = {"competition": int(competition), "cells": {}}
    except (OSError, ValueError) as exc:
        raise agent.Stop(f"Cannot read existing freeze manifest: {exc}") from exc
    if (
        not isinstance(manifest, dict)
        or manifest.get("competition") != int(competition)
        or not isinstance(manifest.get("cells"), dict)
    ):
        raise agent.Stop("Existing freeze manifest has an invalid format or competition id.")

    log.info("Copying %d passing application(s).", len(candidates))
    for candidate in candidates:
        namespace = require_namespace(candidate.get("namespace"))
        identifier = int(candidate["id"])
        label = candidate.get("cell") or candidate.get("team") or identifier
        existing = manifest["cells"].get(namespace)
        if existing:
            if existing.get("submission_id") != identifier:
                raise agent.Stop(
                    f"{namespace} already has a freeze record for another submission."
                )
            image_digest = existing.get("image_digest")
            extra_digests = existing.get("extra_digests")
            snapshot_path = existing.get("seed_snapshot", "")
            snapshot_hash = existing.get("seed_sha256", "")
            if (
                not isinstance(image_digest, str)
                or not re.fullmatch(r"[^@\s]+@sha256:[0-9a-f]{64}", image_digest)
                or not image_digest.startswith(f"{prefix}/")
                or not isinstance(extra_digests, list)
                or len(extra_digests) != len(candidate.get("extra_images") or [])
                or any(
                    not isinstance(digest, str)
                    or not re.fullmatch(r"[^@\s]+@sha256:[0-9a-f]{64}", digest)
                    or not digest.startswith(f"{prefix}/")
                    for digest in extra_digests
                )
            ):
                raise agent.Stop(f"{namespace} has an invalid existing freeze record.")
            if candidate.get("needs_database"):
                if snapshot_path != f"seed/{namespace}.sql" or not re.fullmatch(
                    r"[0-9a-f]{64}", snapshot_hash
                ):
                    raise agent.Stop(f"{namespace} has an invalid database snapshot record.")
                snapshot_file = directory / snapshot_path
                try:
                    seed_data = snapshot_file.read_bytes()
                except OSError as exc:
                    raise agent.Stop(
                        f"Cannot read existing seed snapshot for {namespace}: {exc}"
                    ) from exc
                if hashlib.sha256(seed_data).hexdigest() != snapshot_hash:
                    raise agent.Stop(f"Seed snapshot checksum failed for {namespace}.")
            elif snapshot_path or snapshot_hash:
                raise agent.Stop(f"{namespace} has an unexpected database snapshot.")
        else:
            repo_name = re.sub(r"[^a-z0-9-]+", "-", namespace.lower()).strip("-")
            image_tag = (
                f"{prefix}/{repo_name}:competition-{competition}-submission-{identifier}"
            )
            with tempfile.TemporaryDirectory(prefix="jengasec-freeze-") as workdir:
                image_digest = copy_image(
                    candidate, candidate["image_reference"], image_tag, workdir
                )
                extra_digests = []
                for index, extra in enumerate(candidate.get("extra_images") or [], start=1):
                    if not isinstance(extra, str) or not extra:
                        raise agent.Stop(f"{label} has an invalid extra image reference.")
                    extra_tag = (
                        f"{prefix}/{repo_name}:competition-{competition}-submission-"
                        f"{identifier}-extra-{index}"
                    )
                    extra_digests.append(copy_image(candidate, extra, extra_tag, workdir))

            snapshot_path = ""
            snapshot_hash = ""
            if candidate.get("needs_database"):
                dump = dump_database(namespace)
                snapshot_path = f"seed/{namespace}.sql"
                snapshot_file = directory / snapshot_path
                atomic_write(snapshot_file, dump)
                snapshot_hash = hashlib.sha256(dump.encode()).hexdigest()

            manifest["cells"][namespace] = {
                "submission_id": identifier,
                "image_digest": image_digest,
                "extra_digests": extra_digests,
                "seed_snapshot": snapshot_path,
                "seed_sha256": snapshot_hash,
            }
            atomic_write(manifest_path, json.dumps(manifest, indent=2) + "\n")
        api_post(
            api,
            "/deployments/api/freeze/",
            {
                "id": identifier,
                "digest": image_digest,
                "extra_digests": extra_digests,
            },
        )
        log.info("[%s] frozen at %s", label, image_digest)

    if missing:
        raise agent.Stop(
            f"{len(missing)} application team(s) have no passing submission; "
            "see the NO PASSING IMAGE entries above."
        )
    log.info("Freeze complete for competition %s.", competition)


def _kubectl_apply(documents, namespace=None):
    if not documents:
        return
    body = json.dumps({"apiVersion": "v1", "kind": "List", "items": documents})
    command = ["apply", "-f", "-"]
    code, _, error = agent.kubectl(command, stdin=body)
    if code:
        target = f" in {namespace}" if namespace else ""
        raise agent.Infra(f"kubectl apply failed{target}: {error.strip()[:400]}")


def _registry_pull_secret(registry, namespace):
    username = os.environ.get("JENGASEC_REGISTRY_USERNAME", "")
    password = os.environ.get("JENGASEC_REGISTRY_PASSWORD", "")
    auths = {}
    if bool(username) != bool(password):
        raise agent.Stop(
            "Set both JENGASEC_REGISTRY_USERNAME and JENGASEC_REGISTRY_PASSWORD, "
            "or leave both unset for an anonymous private registry."
        )
    if username:
        host = registry.split("/", 1)[0]
        auths[host] = {
            "auth": base64.b64encode(f"{username}:{password}".encode()).decode()
        }
    docker_config = json.dumps({"auths": auths})
    return {
        "apiVersion": "v1",
        "kind": "Secret",
        "type": "kubernetes.io/dockerconfigjson",
        "metadata": {"name": "team-registry", "namespace": namespace},
        "data": {".dockerconfigjson": base64.b64encode(docker_config.encode()).decode()},
    }


def reset_day(api, competition, day, state_dir, registry, dry_run=False, scope_script=None):
    response = api_get(api, f"/deployments/api/range-state/?competition={int(competition)}")
    cells = response.get("cells")
    if not isinstance(cells, list):
        raise agent.Stop("The platform returned an invalid range-state response.")
    directory = pathlib.Path(state_dir) / f"competition-{int(competition)}"
    manifest_path = directory / "freeze.json"
    try:
        with open(manifest_path, encoding="utf-8") as handle:
            manifest = json.load(handle)
    except (OSError, ValueError) as exc:
        raise agent.Stop(f"Cannot read frozen image/seed manifest: {exc}") from exc
    if not dry_run:
        registry = registry_prefix(registry)
        if not scope_script:
            raise agent.Stop(
                "Set JENGASEC_MAKE_SCOPE to the existing make-scope.py path before resetting."
            )
        script_path = pathlib.Path(scope_script)
        if not script_path.is_file():
            raise agent.Stop(f"Scope script does not exist: {script_path}")

    selected = []
    for cell in cells:
        namespace = require_namespace(cell.get("namespace"))
        digest = cell.get("image_digest")
        if not digest:
            continue
        if not re.fullmatch(r"[^@\s]+@sha256:[0-9a-f]{64}", digest):
            raise agent.Stop(f"{namespace} has an invalid frozen image digest.")
        entry = manifest.get("cells", {}).get(namespace)
        if not entry or entry.get("image_digest") != digest:
            raise agent.Stop(f"No matching local freeze snapshot is recorded for {namespace}.")
        if not dry_run and not digest.startswith(f"{registry}/"):
            raise agent.Stop(f"{namespace} is not pinned to the configured JengaSec registry.")
        agent.require_service_ip(cell.get("service_ip"))
        expected_seed_name = f"seed/{namespace}.sql"
        seed_name = entry.get("seed_snapshot", "")
        if seed_name and seed_name != expected_seed_name:
            raise agent.Stop(f"Unsafe seed snapshot path recorded for {namespace}.")
        if cell.get("needs_database") and not seed_name:
            raise agent.Stop(f"No frozen database snapshot is recorded for {namespace}.")
        if seed_name:
            seed_path = directory / seed_name
            try:
                seed_data = seed_path.read_bytes()
            except OSError as exc:
                raise agent.Stop(f"Cannot read seed snapshot for {namespace}: {exc}") from exc
            if hashlib.sha256(seed_data).hexdigest() != entry.get("seed_sha256"):
                raise agent.Stop(f"Seed snapshot checksum failed for {namespace}.")
        selected.append((cell, namespace, entry))

    if not selected:
        raise agent.Stop(f"No frozen cells found for competition {competition}.")
    if dry_run:
        for cell, namespace, entry in selected:
            log.info(
                "Would reset %s to %s%s",
                namespace,
                entry["image_digest"],
                " and restore database seed" if entry.get("seed_snapshot") else "",
            )
        log.info("Would reapply scope rules for day %s.", day)
        return

    environment = os.environ.copy()
    environment["JENGASEC_ROUND"] = str(day)
    environment["JENGASEC_COMPETITION_ID"] = str(competition)
    preflight = subprocess.run(
        [sys.executable, str(script_path), "--dry-run"],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
        env=environment,
    )
    if preflight.returncode:
        raise agent.Infra(
            "Scope preflight failed before resetting applications: "
            f"{(preflight.stderr or preflight.stdout).strip()[:400]}"
        )

    for cell, namespace, entry in selected:
        log.info("Resetting %s from its frozen image.", namespace)
        code, _, error = agent.kubectl(
            ["scale", "deployment/app", "-n", namespace, "--replicas=0"]
        )
        if code and "not found" not in error.lower():
            raise agent.Infra(f"Could not stop {namespace}/app: {error.strip()[:300]}")

        submission = {
            "port": cell["port"],
            "service_ip": cell["service_ip"],
            "health_path": cell["health_path"],
            "needs_database": cell["needs_database"],
            "env_vars": cell.get("env_vars") or {},
            "extra_images": entry.get("extra_digests") or [],
        }
        documents = agent.manifests(
            submission, namespace, entry["image_digest"]
        )
        _kubectl_apply(
            [{"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": namespace}}],
            namespace,
        )
        _kubectl_apply([_registry_pull_secret(registry, namespace)], namespace)
        database_documents = [doc for doc in documents if doc["metadata"]["name"] == "db"]
        app_documents = [doc for doc in documents if doc["metadata"]["name"] != "db"]
        _kubectl_apply(database_documents, namespace)

        seed_name = entry.get("seed_snapshot")
        if seed_name:
            code, _, error = agent.kubectl(
                [
                    "rollout", "status", "deployment/db", "-n", namespace,
                    "--timeout=180s",
                ],
                timeout=210,
            )
            if code:
                raise agent.Infra(
                    f"Database in {namespace} did not become ready: {error.strip()[:300]}"
                )
            code, _, error = agent.kubectl(
                [
                    "exec", "deployment/db", "-n", namespace, "--",
                    "dropdb", "-U", "app", "--maintenance-db=postgres", "--if-exists", "app",
                ],
                timeout=60,
            )
            if code:
                raise agent.Infra(
                    f"Could not clear the previous database state in {namespace}: "
                    f"{error.strip()[:300]}"
                )
            code, _, error = agent.kubectl(
                [
                    "exec", "deployment/db", "-n", namespace, "--",
                    "createdb", "-U", "app", "--maintenance-db=postgres", "-O", "app", "app",
                ],
                timeout=60,
            )
            if code:
                raise agent.Infra(
                    f"Could not create a clean database in {namespace}: {error.strip()[:300]}"
                )
            code, _, error = agent.kubectl(
                [
                    "exec", "-i", "deployment/db", "-n", namespace, "--",
                    "psql", "-v", "ON_ERROR_STOP=1", "-U", "app", "-d", "app",
                ],
                stdin=(directory / seed_name).read_text(encoding="utf-8"),
                timeout=300,
            )
            if code:
                raise agent.Infra(
                    f"Could not restore database seed in {namespace}: {error.strip()[:300]}"
                )
        _kubectl_apply(app_documents, namespace)
        agent.wait_healthy(namespace, 300)
        log.info("[%s] reset and healthy for day %s.", namespace, day)

    completed = subprocess.run(
        [sys.executable, str(script_path)],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
        env=environment,
    )
    if completed.returncode:
        raise agent.Infra(
            "Reapplying scope rules failed: "
            f"{(completed.stderr or completed.stdout).strip()[:400]}"
        )
    log.info("Scope rules reapplied after day %s reset.", day)


def parse_wireguard_dump(text, peers, now=None, active_seconds=180):
    now = now or time.time()
    address_to_peer = {}
    for peer in peers:
        address = str(peer.get("address") or "").split("/", 1)[0]
        if address:
            address_to_peer[address] = peer

    observations = {}
    for line in text.splitlines():
        columns = line.split("\t")
        if len(columns) < 8:
            continue
        allowed_ips = columns[3]
        try:
            handshake = int(columns[4])
        except ValueError:
            continue
        for allowed in allowed_ips.split(","):
            address = allowed.split("/", 1)[0]
            peer = address_to_peer.get(address)
            if peer is None:
                continue
            active = 0 <= now - handshake <= active_seconds and handshake > 0
            previous = observations.get(peer["id"])
            if previous is None or handshake > previous["handshake_epoch"]:
                observations[peer["id"]] = {
                    "id": peer["id"],
                    "active": active,
                    "handshake_epoch": handshake,
                    "last_handshake_at": (
                        time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(handshake))
                        if handshake > 0 else None
                    ),
                }
    for peer in peers:
        observations.setdefault(
            peer["id"],
            {"id": peer["id"], "active": False, "handshake_epoch": 0, "last_handshake_at": None},
        )
    return list(observations.values())


def pod_observation(cell):
    namespace = require_namespace(cell["namespace"])
    if not cell.get("image_digest"):
        return {
            "team_id": cell["team_id"],
            "status": "building",
            "healthy": False,
            "image_digest": "",
            "detail": "No frozen image is recorded.",
        }
    try:
        expected_service_ip = agent.require_service_ip(cell.get("service_ip"))
    except agent.Stop as exc:
        return {
            "team_id": cell["team_id"],
            "status": "down",
            "healthy": False,
            "image_digest": "",
            "detail": str(exc),
        }
    code, output, error = agent.kubectl(
        ["get", "deployment", "app", "-n", namespace, "-o", "json"], timeout=60
    )
    if code:
        return {
            "team_id": cell["team_id"],
            "status": "down",
            "healthy": False,
            "image_digest": "",
            "detail": f"Deployment query failed: {error.strip()[:1000]}",
        }
    try:
        deployment = json.loads(output)
    except ValueError:
        raise agent.Infra(f"kubectl returned invalid deployment JSON for {namespace}.")
    spec = deployment.get("spec", {})
    status = deployment.get("status", {})
    expected_image = cell["image_digest"]
    extra_images = cell.get("extra_digests") or []
    try:
        agent.validate_workload_images(
            spec.get("template", {}).get("spec", {}).get("containers", []),
            expected_image=expected_image, extra_images=extra_images,
        )
    except agent.Infra:
        return {
            "team_id": cell["team_id"],
            "status": "down",
            "healthy": False,
            "image_digest": "",
            "detail": "Deployment image does not match the frozen digest.",
        }
    ready = (
        status.get("readyReplicas", 0) >= spec.get("replicas", 1)
        and status.get("availableReplicas", 0) >= spec.get("replicas", 1)
    )
    observed_digest = ""
    code, output, error = agent.kubectl(
        ["get", "pods", "-n", namespace, "-l", "app=app", "-o", "json"], timeout=60
    )
    if code:
        ready = False
        detail = f"Pod query failed: {error.strip()[:1000]}"
    else:
        try:
            pods = json.loads(output).get("items", [])
        except ValueError:
            raise agent.Infra(f"kubectl returned invalid pod JSON for {namespace}.")
        containers = [
            container
            for pod in pods
            for container in pod.get("status", {}).get("containerStatuses", [])
            if container.get("name") == "app"
        ]
        ready = ready and bool(containers) and all(
            container.get("ready") for container in containers
        )
        detail = "Application pods are ready." if ready else "Application pods are not ready."
        try:
            observed_digest = agent.running_artifact(pods, extra_images=extra_images)
        except agent.Infra as exc:
            ready = False
            detail = str(exc)
    code, output, error = agent.kubectl(
        ["get", "service", "app", "-n", namespace, "-o", "json"], timeout=60
    )
    if code:
        ready = False
        detail = f"Service query failed: {error.strip()[:1000]}"
    else:
        try:
            service = json.loads(output)
        except ValueError:
            raise agent.Infra(f"kubectl returned invalid service JSON for {namespace}.")
        service_spec = service.get("spec", {})
        ingress = service.get("status", {}).get("loadBalancer", {}).get("ingress", [])
        assigned_ips = [entry.get("ip") for entry in ingress if entry.get("ip")]
        if (
            service_spec.get("type") != "LoadBalancer"
            or service_spec.get("loadBalancerIP") != expected_service_ip
            or expected_service_ip not in assigned_ips
        ):
            ready = False
            detail = f"MetalLB service is not assigned to {expected_service_ip}."
    if observed_digest and expected_image.rsplit("@", 1)[-1] != observed_digest:
        ready = False
        detail = "Running pod image digest differs from the frozen digest."
    return {
        "team_id": cell["team_id"],
        "status": "live" if ready else "down",
        "healthy": bool(ready),
        "image_digest": observed_digest,
        "extra_digests": [image.rsplit("@", 1)[1] for image in extra_images] if observed_digest else [],
        "detail": detail,
    }


def reconcile(api, wg_command="wg show all dump", active_seconds=180, competition=None):
    path = "/deployments/api/range-state/"
    if competition is not None:
        path += f"?competition={int(competition)}"
    response = api_get(api, path)
    cells, peers = response.get("cells"), response.get("peers")
    if not isinstance(cells, list) or not isinstance(peers, list):
        raise agent.Stop("The platform returned an invalid range-state response.")
    command = shlex.split(wg_command)
    if not command:
        raise agent.Stop("The WireGuard dump command cannot be empty.")
    try:
        code, output, error = agent.run(command, timeout=60)
    except agent.Infra as exc:
        raise agent.Infra(f"WireGuard query failed: {exc}") from exc
    if code:
        raise agent.Infra(f"WireGuard query failed: {error.strip()[:300]}")
    wg_observations = parse_wireguard_dump(
        output, peers, active_seconds=active_seconds
    )
    cell_observations = [pod_observation(cell) for cell in cells]
    result = api_post(
        api,
        "/deployments/api/range-state/",
        {"cells": cell_observations, "peers": wg_observations},
    )
    log.info(
        "Reconciled %d application(s) and %d WireGuard peer(s).",
        result.get("updated_cells", 0),
        result.get("updated_peers", 0),
    )
    return result
