#!/usr/bin/env python3
"""JengaSec deploy agent: turns a team's image reference into a running cell.

Runs on Server 3 (the `shared` VM), not on the website. Pulling, scanning
and deploying need Docker, Trivy and a cluster, which the public site does
not have and should not have; and Server 3 is never exposed to the
internet (ADR 0002). So the work happens here and the agent *polls* the
website for it:

    website  <--- GET  /deployments/api/pending/   (what to pull)
             <--- POST /deployments/api/status/    (what happened)

Outbound HTTPS only, in one direction. That holds whether the site is on
Render or on the web VM; only --base changes.

What it does to each submission, in the order the team was promised:

    Pulled    logged in with their token and copied the image
    Scanned   Trivy; critical findings stop it
    Deployed  applied to the cluster in the team's namespace
    Healthy   their health path answered 200 -- this is a pass

Every stage is reported as it finishes, so a team watching its page sees
progress rather than a long silence. A failure posts `failed` with the
reason written for the team, not for us.

Standard library only: Server 3 should not need a pip install to run the
thing the whole build phase depends on.

    python3 agent.py --once                 # one pass, for cron
    python3 agent.py --watch --interval 30  # stay up
    python3 agent.py --once --no-deploy     # pull and scan only
"""
import argparse
import base64
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

log = logging.getLogger("deploy-agent")

DEFAULT_BASE = "https://www.jengasec.com"
# architecture section 2.3: jengasec-<enterprise>-<cell>, e.g. jengasec-ent-a-app01
DEFAULT_NAMESPACE = "jengasec-ent-{enterprise}-{cell}"
# Guide: 2 CPU and 3 GB in total for the cell, database included.
APP_CPU, APP_MEMORY = "1500m", "2Gi"
DB_CPU, DB_MEMORY = "500m", "1Gi"
MAX_IMAGE_BYTES = 2 * 1024 * 1024 * 1024  # "Keep each image under 2 GB"


class Stop(Exception):
    """This submission cannot go further. The message is shown to the team."""

    def __init__(self, message, status="failed"):
        super().__init__(message)
        self.status = status


class Infra(Exception):
    """Our side is broken, not the team's submission.

    Raised when Docker is down, Trivy is missing a database, the cluster
    is unreachable -- anything the team can do nothing about. The
    submission is deliberately **left alone**, so it stays in the queue
    and is picked up on the next pass once we have fixed the host. It is
    never marked failed, because a team seeing "failed" when their image
    was fine would resubmit a perfectly good image, and at 13:50 on the
    day that is the last thing anyone needs.
    """


# Errors that mean the deploy host, not the submission.
INFRA_SIGNS = (
    "cannot connect to the docker daemon",
    "error during connect",
    "docker api at npipe",
    "docker api at unix",
    "is the docker daemon running",
    "no space left on device",
    "connection refused",
)


def is_infra_error(text):
    lowered = (text or "").lower()
    return any(sign in lowered for sign in INFRA_SIGNS)


# --------------------------------------------------------------- plumbing


def run(cmd, *, stdin=None, timeout=600, check=False):
    """Run a command. Returns (code, stdout, stderr).

    Nothing sensitive is ever passed in argv -- tokens go in on stdin or
    in a file -- because argv is visible to every user via `ps`.
    """
    log.debug("$ %s", " ".join(cmd))
    try:
        done = subprocess.run(
            cmd, input=stdin, capture_output=True, text=True, timeout=timeout
        )
    except FileNotFoundError:
        raise Infra(f"{cmd[0]} is not installed on the deploy host.")
    except subprocess.TimeoutExpired:
        raise Stop(f"{cmd[0]} took longer than {timeout}s and was stopped.")
    if check and done.returncode != 0:
        raise Stop((done.stderr or done.stdout or "").strip()[:500])
    return done.returncode, done.stdout, done.stderr


def have(binary):
    return shutil.which(binary) is not None


class Api:
    """The website's two endpoints."""

    def __init__(self, base, token, timeout=30):
        self.base = base.rstrip("/")
        self.token = token
        self.timeout = timeout

    def _call(self, path, payload=None):
        url = f"{self.base}{path}"
        data = json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(url, data=data, method="POST" if data else "GET")
        request.add_header("X-Deploy-Token", self.token)
        if data:
            request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode(errors="replace")[:300]
            if exc.code == 404:
                raise SystemExit(
                    f"{url} answered 404. Either DJANGO_DEPLOY_TOKEN is unset on the "
                    f"website, or this build does not have the deployment endpoints."
                )
            if exc.code == 403:
                raise SystemExit(f"{url} refused the token. Check JENGASEC_DEPLOY_TOKEN.")
            raise Stop(f"the platform answered {exc.code}: {body}")
        except urllib.error.URLError as exc:
            raise Stop(f"could not reach {self.base}: {exc.reason}")

    def pending(self):
        return self._call("/deployments/api/pending/").get("pending", [])

    def report(self, submission_id, status, *, detail="", scan_report=None, logs=None, resolved_digest=None):
        payload = {"id": submission_id, "status": status, "detail": detail}
        if scan_report is not None:
            payload["scan_report"] = scan_report
        if logs:
            payload["logs_excerpt"] = logs[-4000:]
        if resolved_digest is not None:
            payload["resolved_digest"] = resolved_digest
        return self._call("/deployments/api/status/", payload)

    def active(self, after=0):
        return self._call(f"/deployments/api/active/?after={after}")

    def artifact(self, identifier, resolved_digest):
        return self._call("/deployments/api/artifact/", {"id": identifier, "resolved_digest": resolved_digest})


# ----------------------------------------------------------------- docker


def registry_of(image):
    """The registry host in an image reference, e.g. ghcr.io."""
    head = image.split("/", 1)[0]
    return head if ("." in head or ":" in head) else "docker.io"


def docker_config(submission, directory):
    """An isolated docker config holding just this team's credential.

    Per submission and thrown away afterwards, so one team's token never
    sits in a shared ~/.docker/config.json where the next pull could use
    it by accident.

    With no token -- the platform could not decrypt one, or the team left
    it blank -- the config is written empty and the pull is anonymous.
    Sending a half-formed credential makes the registry answer
    "unauthorized", which would tell the team their token is wrong when
    the truth is that we never had one.
    """
    path = os.path.join(directory, "config.json")
    token = submission.get("pull_token")
    if not token:
        log.warning("Submission %s has no usable token; pulling anonymously.",
                    submission.get("id"))
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"auths": {}}, handle)
        os.chmod(path, 0o600)
        return path

    auth = base64.b64encode(
        f"{submission['registry_username']}:{token}".encode()
    ).decode()
    registry = registry_of(submission["image_reference"])
    host = "https://index.docker.io/v1/" if registry == "docker.io" else registry
    with open(path, "w", encoding="utf-8") as handle:
        json.dump({"auths": {host: {"auth": auth}}}, handle)
    os.chmod(path, 0o600)
    return path


def docker(args, *, config_dir=None, timeout=900):
    env_prefix = []
    environment = os.environ.copy()
    if config_dir:
        environment["DOCKER_CONFIG"] = config_dir
    log.debug("$ docker %s", " ".join(args))
    try:
        done = subprocess.run(
            ["docker", *args], capture_output=True, text=True,
            timeout=timeout, env=environment,
        )
    except FileNotFoundError:
        raise Infra("Docker is not installed on the deploy host.")
    except subprocess.TimeoutExpired:
        raise Stop(f"docker {args[0]} took longer than {timeout}s and was stopped.")
    if done.returncode != 0 and is_infra_error(done.stderr):
        raise Infra(f"docker {args[0]}: {(done.stderr or '').strip()[:300]}")
    return done.returncode, done.stdout, done.stderr


def is_public(image):
    """True when the image can be fetched with no credentials at all.

    The Guide rejects public images: a Red team could download and study
    the application before the event. Checked with an empty docker config
    so the team's own credential cannot make a public image look private.
    """
    with tempfile.TemporaryDirectory() as empty:
        code, _, _ = docker(["manifest", "inspect", image], config_dir=empty, timeout=120)
        return code == 0


def pull(submission, config_dir):
    image = submission["image_reference"]
    code, _, err = docker(["pull", "--platform", "linux/amd64", image], config_dir=config_dir)
    if code != 0:
        text = (err or "").lower()
        if "unauthorized" in text or "denied" in text or "authentication" in text:
            raise Stop(
                "We could not log in to your registry. Create a new read-only token "
                "(GitHub: a classic token with read:packages) and update it on your "
                "team page. Check the registry username too."
            )
        if "not found" in text or "manifest unknown" in text:
            raise Stop(
                f"{image} does not exist in that registry. Check the name and the tag."
            )
        raise Stop(f"Pull failed: {(err or '').strip()[:300]}")


def inspect(image):
    """The checks that do not need a cluster: architecture, user, size."""
    code, out, err = docker([
        "image", "inspect", image,
        "--format", "{{.Architecture}}|{{.Os}}|{{.Config.User}}|{{.Size}}",
    ])
    if code != 0:
        raise Stop(f"Could not inspect the image: {(err or '').strip()[:200]}")
    architecture, os_name, user, size = out.strip().split("|")

    if architecture != "amd64" or os_name != "linux":
        raise Stop(
            f"This image is built for {os_name}/{architecture}, and the range runs "
            f"linux/amd64 — it will not start. If you build on an Apple Silicon Mac, "
            f"rebuild with: docker buildx build --platform linux/amd64",
            status="rejected",
        )
    if user.strip() in ("", "root", "0"):
        raise Stop(
            "The image runs as root. Add a non-root user in your Dockerfile "
            "(RUN useradd --create-home appuser, then USER appuser) and rebuild.",
            status="rejected",
        )
    if int(size) > MAX_IMAGE_BYTES:
        gigabytes = int(size) / (1024 ** 3)
        raise Stop(
            f"The image is {gigabytes:.1f} GB; the limit is 2 GB. A -slim base image "
            f"and a multi-stage build are usually enough.",
            status="rejected",
        )
    return {"architecture": architecture, "user": user, "size_bytes": int(size)}


def pinned_reference(image):
    """Resolve the pulled registry manifest, not Docker's image-config ID."""
    code, out, _ = docker(["image", "inspect", image, "--format", "{{json .RepoDigests}}"])
    if code:
        raise Infra("Cannot read the pulled image's registry digest.")
    repository = image.split("@", 1)[0]
    tail = repository.rsplit("/", 1)[-1]
    if ":" in tail:
        repository = repository.rsplit(":", 1)[0]
    try:
        candidates = [value for value in json.loads(out) if isinstance(value, str)
                      and value.startswith(repository + "@sha256:")
                      and re.fullmatch(r".+@sha256:[a-f0-9]{64}", value)]
    except (TypeError, ValueError):
        raise Infra("Cannot parse the pulled registry digest.") from None
    if len(set(candidates)) != 1:
        raise Infra("The pulled registry digest is missing or ambiguous.")
    return candidates[0]


def observed_artifact(namespace):
    """Read the pinned workload template independently of readiness."""
    code, out, _ = kubectl(["get", "deployment", "app", "-n", namespace, "-o", "json"], timeout=15)
    if code:
        raise Infra("Cannot verify the assigned application deployment.")
    try:
        containers = json.loads(out)["spec"]["template"]["spec"]["containers"]
        matches = [row["image"] for row in containers if row.get("name") == "app"]
        if len(matches) != 1 or not re.fullmatch(r".+@sha256:[a-f0-9]{64}", matches[0]):
            raise ValueError
        return matches[0].rsplit("@", 1)[1]
    except (ValueError, TypeError, KeyError):
        raise Infra("The deployed application is not pinned to a verified digest.") from None


def verify_artifacts(api, options):
    after = 0
    while True:
        response = api.active(after)
        for row in response["active"]:
            if options.only and row["id"] != options.only:
                continue
            namespace = namespace_for(row, options.namespace_template)
            # A missing deployment stops freshness updates; it does not fabricate
            # an app failure or refresh an earlier digest from local memory.
            try:
                value = observed_artifact(namespace)
                api.artifact(row["id"], value)
            except (Infra, Stop):
                log.warning("Artifact confirmation unavailable for submission %s", row["id"])
        following = response["next_cursor"]
        if following <= after or len(response["active"]) < 100:
            break
        after = following


def scan(image, *, fail_on_critical=True):
    """Trivy. Returns (report, blocking_message_or_None)."""
    if not have("trivy"):
        log.warning("Trivy is not installed; recording the scan as skipped.")
        return {"skipped": "Trivy is not installed on the deploy host."}, None

    code, out, err = run([
        "trivy", "image", "--quiet", "--format", "json",
        "--severity", "HIGH,CRITICAL", "--ignore-unfixed", image,
    ], timeout=900)
    if code not in (0, 1) or not out.strip():
        return {"error": (err or "trivy produced no output").strip()[:300]}, None

    try:
        data = json.loads(out)
    except ValueError:
        return {"error": "trivy output was not JSON"}, None

    counts = {"CRITICAL": 0, "HIGH": 0}
    worst = []
    for result in data.get("Results") or []:
        for item in result.get("Vulnerabilities") or []:
            severity = item.get("Severity", "")
            if severity in counts:
                counts[severity] += 1
                if severity == "CRITICAL" and len(worst) < 10:
                    worst.append({
                        "id": item.get("VulnerabilityID"),
                        "package": item.get("PkgName"),
                        "fixed_in": item.get("FixedVersion"),
                    })
    report = {"critical": counts["CRITICAL"], "high": counts["HIGH"], "worst": worst}
    if fail_on_critical and counts["CRITICAL"]:
        return report, (
            f"The scan found {counts['CRITICAL']} critical vulnerability(ies) with a fix "
            f"available. Update your base image (for example a newer -slim tag), rebuild "
            f"and resubmit. If one is a deliberate part of your design, tell the "
            f"organisers and a judge can accept it."
        )
    return report, None


# ----------------------------------------------------------- kubernetes


def namespace_for(submission, template):
    return template.format(
        enterprise=submission["enterprise"], cell=(submission["cell"] or "unassigned").lower()
    )


def manifests(submission, namespace, image):
    """Deployment, Service and -- when asked for -- a PostgreSQL 16.

    The team's own health path becomes the readiness probe, so "Healthy"
    is decided by the same check they were told about rather than by a
    separate request from somewhere else.
    """
    port = int(submission["port"])
    health = submission["health_path"]
    env = [{"name": key, "value": str(value)} for key, value in (submission.get("env_vars") or {}).items()]
    docs = []

    if submission.get("needs_database"):
        env += [
            {"name": "DATABASE_URL", "value": "postgresql://app:app@db:5432/app"},
            {"name": "PGHOST", "value": "db"}, {"name": "PGPORT", "value": "5432"},
            {"name": "PGDATABASE", "value": "app"},
            {"name": "PGUSER", "value": "app"}, {"name": "PGPASSWORD", "value": "app"},
        ]
        docs += [
            {
                "apiVersion": "apps/v1", "kind": "Deployment",
                "metadata": {"name": "db", "namespace": namespace},
                "spec": {
                    "replicas": 1,
                    "selector": {"matchLabels": {"app": "db"}},
                    "template": {
                        "metadata": {"labels": {"app": "db"}},
                        "spec": {"containers": [{
                            "name": "db", "image": "postgres:16",
                            "env": [
                                {"name": "POSTGRES_DB", "value": "app"},
                                {"name": "POSTGRES_USER", "value": "app"},
                                {"name": "POSTGRES_PASSWORD", "value": "app"},
                            ],
                            "ports": [{"containerPort": 5432}],
                            "resources": {"limits": {"cpu": DB_CPU, "memory": DB_MEMORY}},
                        }]},
                    },
                },
            },
            {
                "apiVersion": "v1", "kind": "Service",
                "metadata": {"name": "db", "namespace": namespace},
                "spec": {"selector": {"app": "db"}, "ports": [{"port": 5432, "targetPort": 5432}]},
            },
        ]

    docs += [
        {
            "apiVersion": "apps/v1", "kind": "Deployment",
            "metadata": {"name": "app", "namespace": namespace},
            "spec": {
                "replicas": 1,
                "selector": {"matchLabels": {"app": "app"}},
                "template": {
                    "metadata": {"labels": {"app": "app"}},
                    "spec": {
                        "imagePullSecrets": [{"name": "team-registry"}],
                        "containers": [{
                            "name": "app", "image": image,
                            "ports": [{"containerPort": port}],
                            "env": env + [{"name": "PORT", "value": str(port)}],
                            "readinessProbe": {
                                "httpGet": {"path": health, "port": port},
                                "initialDelaySeconds": 5, "periodSeconds": 5,
                                "failureThreshold": 30,
                            },
                            "resources": {"limits": {"cpu": APP_CPU, "memory": APP_MEMORY}},
                            "securityContext": {
                                "allowPrivilegeEscalation": False,
                                "readOnlyRootFilesystem": False,
                            },
                        }],
                    },
                },
            },
        },
        {
            "apiVersion": "v1", "kind": "Service",
            "metadata": {"name": "app", "namespace": namespace},
            "spec": {"selector": {"app": "app"}, "ports": [{"port": 80, "targetPort": port}]},
        },
    ]
    return docs


def kubectl(args, *, stdin=None, timeout=600):
    return run(["kubectl", *args], stdin=stdin, timeout=timeout)


def deploy(submission, namespace, image, config_path):
    """Create the namespace, the pull secret and the workloads."""
    kubectl(["create", "namespace", namespace])  # already there is fine

    # The pull secret goes in from a file, never argv.
    with open(config_path, encoding="utf-8") as handle:
        dockerconfig = handle.read()
    secret = {
        "apiVersion": "v1", "kind": "Secret", "type": "kubernetes.io/dockerconfigjson",
        "metadata": {"name": "team-registry", "namespace": namespace},
        "data": {".dockerconfigjson": base64.b64encode(dockerconfig.encode()).decode()},
    }
    code, _, err = kubectl(["apply", "-f", "-"], stdin=json.dumps(secret))
    if code != 0:
        raise Stop(f"Could not create the pull secret: {err.strip()[:200]}")

    body = json.dumps({"apiVersion": "v1", "kind": "List",
                       "items": manifests(submission, namespace, image)})
    code, _, err = kubectl(["apply", "-f", "-"], stdin=body)
    if code != 0:
        raise Stop(f"Could not apply the manifests: {err.strip()[:300]}")


def wait_healthy(namespace, timeout):
    code, _, err = kubectl(
        ["rollout", "status", "deployment/app", "-n", namespace, f"--timeout={timeout}s"],
        timeout=timeout + 60,
    )
    logs = recent_logs(namespace)
    if code != 0:
        raise Stop(
            "Your app started but its health check never passed. Check the port and "
            "the health-check path on your team page against what the app really "
            "listens on, and look at the logs below. "
            f"({err.strip()[:200]})",
        )
    return logs


def recent_logs(namespace, lines=50):
    _, out, _ = kubectl(
        ["logs", "deployment/app", "-n", namespace, f"--tail={lines}"], timeout=60
    )
    return out or ""


# ---------------------------------------------------------------- the run


def process(submission, api, options):
    """One submission, all the way through.

    Raises only `Infra`, which the caller treats as "stop the pass and
    leave everything queued". Every other failure is reported against
    this submission and the next one is tried.
    """
    identifier = submission["id"]
    label = f"{submission.get('team') or submission.get('cell') or identifier}"
    log.info("[%s] %s", label, submission["image_reference"])

    workdir = tempfile.mkdtemp(prefix="jengasec-deploy-")
    try:
        config_path = docker_config(submission, workdir)
        image = submission["image_reference"]

        # ---- Pulled
        if not options.allow_public and is_public(image):
            raise Stop(
                "This image is public, so any Red team could download and study your "
                "application before the event. Set the package visibility to Private "
                "in your registry and resubmit.",
                status="rejected",
            )
        pull(submission, workdir)
        details = inspect(image)
        image = pinned_reference(image)
        resolved_digest = image.rsplit("@", 1)[1]
        api.report(identifier, "pulled",
                   detail=f"{details['size_bytes'] // (1024*1024)} MB, runs as {details['user']}",
                   resolved_digest=resolved_digest)
        log.info("[%s] pulled", label)

        # ---- Scanned
        report, blocking = scan(image, fail_on_critical=not options.allow_critical)
        if blocking:
            api.report(identifier, "rejected", detail=blocking, scan_report=report)
            log.info("[%s] rejected on scan", label)
            return
        # "0 critical, 0 high" must never stand in for a scan that did not
        # run: it would tell a team their image is clean when nothing
        # looked at it.
        if report.get("skipped") or report.get("error"):
            scanned_detail = "Vulnerability scan not available; recorded as not scanned."
        else:
            scanned_detail = (
                f"{report.get('critical', 0)} critical, {report.get('high', 0)} high"
            )
        api.report(identifier, "scanned", scan_report=report, detail=scanned_detail)
        log.info("[%s] scanned", label)

        if options.no_deploy or not have("kubectl"):
            if not options.no_deploy:
                log.warning("[%s] kubectl is not installed; stopping after the scan.", label)
            return

        # ---- Deployed, then Healthy
        namespace = namespace_for(submission, options.namespace_template)
        deploy(submission, namespace, image, config_path)
        api.report(identifier, "deployed", detail=f"Running in {namespace}.")
        log.info("[%s] deployed to %s", label, namespace)

        logs = wait_healthy(namespace, options.health_timeout)
        if observed_artifact(namespace) != resolved_digest:
            raise Infra("The deployed artifact differs from the verified image.")
        api.report(identifier, "healthy",
                   detail=f"{submission['health_path']} answered on port {submission['port']}.",
                   logs=logs, resolved_digest=resolved_digest)
        log.info("[%s] healthy", label)

    except Infra:
        # Our fault. Leave the submission exactly as it was so the next
        # pass retries it, and let the caller stop the pass.
        raise
    except Stop as stop:
        logs = ""
        try:
            if have("kubectl"):
                logs = recent_logs(namespace_for(submission, options.namespace_template))
        except Exception:  # noqa: BLE001 — best effort, the message matters more
            pass
        api.report(identifier, stop.status, detail=str(stop), logs=logs)
        log.info("[%s] %s: %s", label, stop.status, stop)
    except Exception as exc:  # noqa: BLE001 — one bad submission must not stop the rest
        log.exception("[%s] unexpected failure", label)
        try:
            api.report(
                identifier, "failed",
                detail="Something went wrong on our side while checking your image. "
                       "The organisers have been alerted; you do not need to do anything.",
            )
        except Exception:  # noqa: BLE001
            log.error("[%s] could not even report the failure: %s", label, exc)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
        try:
            docker(["image", "rm", "-f", submission["image_reference"]], timeout=120)
        except Exception:  # noqa: BLE001 — disk hygiene only
            pass


def one_pass(api, options):
    if getattr(options, "verify_artifacts", False):
        verify_artifacts(api, options)
    try:
        waiting = api.pending()
    except Stop as stop:
        log.error("Could not fetch work: %s", stop)
        return 0
    if not waiting:
        log.info("Nothing waiting.")
        return 0
    log.info("%d submission(s) waiting.", len(waiting))
    done = 0
    for submission in waiting:
        if options.only and submission["id"] != options.only:
            continue
        try:
            process(submission, api, options)
        except Infra as broken:
            log.error(
                "Deploy host problem, so nothing more this pass and no team was told: %s",
                broken,
            )
            log.error("%d submission(s) left queued. Fix the host and they retry.",
                      len(waiting) - done)
            return done
        done += 1
    return done


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--verify-artifacts", action="store_true",
                        help="Refresh frozen workload identity each pass for scoring.")
    parser.add_argument("--base", default=os.environ.get("JENGASEC_BASE", DEFAULT_BASE),
                        help="Website base URL (env JENGASEC_BASE).")
    parser.add_argument("--token", default=os.environ.get("JENGASEC_DEPLOY_TOKEN", ""),
                        help="Shared secret (env JENGASEC_DEPLOY_TOKEN). Prefer the env var.")
    parser.add_argument("--once", action="store_true", help="One pass, then exit (for cron).")
    parser.add_argument("--watch", action="store_true", help="Keep polling.")
    parser.add_argument("--interval", type=int, default=30, help="Seconds between polls.")
    parser.add_argument("--only", type=int, help="Process just this submission id.")
    parser.add_argument("--no-deploy", action="store_true", help="Pull and scan only.")
    parser.add_argument("--allow-public", action="store_true",
                        help="Do not reject public images (testing only).")
    parser.add_argument("--allow-critical", action="store_true",
                        help="Do not reject on critical vulnerabilities.")
    parser.add_argument("--namespace-template", default=DEFAULT_NAMESPACE)
    parser.add_argument("--health-timeout", type=int, default=180,
                        help="Seconds to wait for the health check.")
    parser.add_argument("-v", "--verbose", action="store_true")
    options = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if options.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S",
    )
    if not options.token:
        parser.error("No token. Set JENGASEC_DEPLOY_TOKEN, the same value as "
                     "DJANGO_DEPLOY_TOKEN on the website.")
    if not have("docker"):
        parser.error("Docker is not installed. This agent runs on the deploy host.")
    for missing, what in (("trivy", "scans are recorded as skipped"),
                          ("kubectl", "nothing is deployed")):
        if not have(missing):
            log.warning("%s is not installed, so %s.", missing, what)

    api = Api(options.base, options.token)
    log.info("Agent up against %s", options.base)

    if not options.watch:
        one_pass(api, options)
        return 0
    while True:
        try:
            one_pass(api, options)
        except KeyboardInterrupt:
            log.info("Stopping.")
            return 0
        except Exception:  # noqa: BLE001 — the loop outlives any one failure
            log.exception("Pass failed; carrying on.")
        time.sleep(options.interval)


if __name__ == "__main__":
    sys.exit(main())
