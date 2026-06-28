# RAG Platform — Helm Chart

Deploys `monolith-service`, `evaluation-service` (api + worker + beat),
`worker-service`, and `rag-ui` to Kubernetes. All four Services are
currently `ClusterIP` only — **no public-facing access exists yet**.

PostgreSQL, Redis, and Keycloak are treated as **external** — this chart
does not deploy them. It only wires their connection details in via
ConfigMap/Secret values so the services inside the cluster can reach them.

### TLS / public access — handed off, not part of this chart

A teammate owns TLS & secrets management as a separate task, including the
Ingress resource and cert-manager `Certificate`/`ClusterIssuer` setup. The
handoff point is the **`rag-ui` Service** (`{{ .Release.Name }}-rag-ui`,
port 80) — that's what the Ingress should route to, since `rag-ui`'s nginx
is what terminates the browser-facing connection and proxies `/api/*`
internally to `monolith-service`. `monolith-service` itself stays
internal-only and is never targeted by an Ingress directly.

---

## Prerequisites (do these once, before the first install)

### 1. Images are built and pushed automatically — you don't build them locally

`.github/workflows/docker-build-push.yml` builds all 4 images
(`rag-base`, `rag-monolith`, `rag-worker`, `rag-evaluation`) and pushes
them to Docker Hub under the `rag-system` namespace on every push to
`main`. Each image gets two tags: `latest` and the 7-character commit SHA.

You don't need Docker installed locally just to deploy — `helm install`
pulls the images directly from Docker Hub (`docker.io/rag-system/...`,
public repos, no `imagePullSecrets` needed).

To find the exact SHA tag from a given push, check the workflow run's
summary on GitHub (Actions tab → the run → Summary) — it lists all four
image:tag pairs it just pushed.

Only build locally if you're testing a Dockerfile change before pushing:

```bash
# Run from the REPO ROOT — not from infra/docker/.
# Required because services/monolith/main.py computes its ROOT path as
# two directories above itself, so the build context must mirror the
# full repo layout.
docker build -f infra/docker/Dockerfile.base       -t rag-base:dev       .
docker build -f infra/docker/Dockerfile.monolith   -t rag-monolith:dev   .
docker build -f infra/docker/Dockerfile.worker      -t rag-worker:dev    .
docker build -f infra/docker/Dockerfile.evaluation  -t rag-evaluation:dev .
```

### 2. Which tag to deploy

- `values-dev.yaml` → image tag stays `latest` (fast local iteration, the
  exact build doesn't need to be pinned)
- `values-staging.yaml` / `values-prod.yaml` → **replace the placeholder
  `tag: latest` with a real commit SHA** before deploying. Pinning matters
  here: `latest` can change under you while a deployment is still running,
  and you lose the ability to say precisely which build is live or roll
  back to a known-good one. Get the SHA from the Actions run summary.

### 3. Create the Secret (NOT created by this chart, on purpose)

Secrets (GROQ_API_KEY, DB credentials, JWT keys, AGE_DATABASE_DSN, etc.)
are intentionally kept OUT of this Helm chart and out of git. Create the
Secret manually before installing:

```bash
kubectl create secret generic rag-platform-secrets \
  --from-env-file=services/monolith/.env
```

Adjust the `--from-env-file` source to wherever your real env values live.
The chart only *references* a Secret named `rag-platform-secrets` via
`envFrom` — it never defines secret values itself. If you change this
name, update `existingSecretName` in each subchart's `values.yaml`.

(TLS via cert-manager and the actual `Certificate`/`ClusterIssuer`
resources are handled separately, in the TLS & secrets management task —
not part of this chart.)

### 4. Build chart dependencies — REQUIRED, easy to forget

This is an **umbrella chart**: `Chart.yaml` declares the three subcharts
(`monolith-service`, `evaluation-service`, `worker-service`) as file-based
dependencies. Helm does NOT automatically discover `charts/` on disk — you
must run `helm dependency build` once (and again any time you change a
subchart's `Chart.yaml` version), or `helm install` will fail or silently
skip the subcharts.

```bash
helm dependency build ./infra/helm/rag-platform
```

---

## Install

```bash
# Local dev cluster
helm install rag-platform ./infra/helm/rag-platform -f infra/helm/rag-platform/values-dev.yaml

# Staging
helm upgrade --install rag-platform ./infra/helm/rag-platform -f infra/helm/rag-platform/values-staging.yaml

# Production
helm upgrade --install rag-platform ./infra/helm/rag-platform -f infra/helm/rag-platform/values-prod.yaml
```

## Upgrade

```bash
helm upgrade rag-platform ./infra/helm/rag-platform -f infra/helm/rag-platform/values-staging.yaml
```

## Uninstall

```bash
helm uninstall rag-platform
```

This removes all Deployments/Services/ConfigMaps/HPAs created by the
chart. It does **not** delete the `rag-platform-secrets` Secret (created
manually, outside the chart) — delete that separately if needed:

```bash
kubectl delete secret rag-platform-secrets
```

## Validate before installing (recommended every time you change a template)

```bash
helm lint ./infra/helm/rag-platform

helm template rag-platform ./infra/helm/rag-platform -f infra/helm/rag-platform/values-dev.yaml
```

`helm template` renders every manifest to stdout without touching the
cluster — use it to catch YAML/templating mistakes before `helm install`
or `helm upgrade` ever talks to the API server.

---

## How this is structured (plain English)

**Umbrella + subcharts.** `infra/helm/rag-platform/Chart.yaml` is the
parent. It doesn't define any Kubernetes resources directly — it just
lists three subcharts as dependencies. Each subchart
(`charts/monolith-service/`, `charts/evaluation-service/`,
`charts/worker-service/`) is a complete, independent chart with its own
`Chart.yaml`, `values.yaml`, and `templates/`. When you run
`helm install`, Helm merges the umbrella's `values-<env>.yaml` into each
subchart's own defaults — e.g. `monolith-service.replicaCount: 3` in
`values-prod.yaml` overrides the `replicaCount: 1` default sitting in
`charts/monolith-service/values.yaml`.

**Why one image, multiple roles, for evaluation-service.**
`evaluation-service` needs to run as a FastAPI app, a Celery worker, AND a
Celery beat scheduler — three different *processes*, but they all come
from the exact same codebase and the exact same `requirements.txt`. Rather
than building three separate images, there's one image
(`rag-evaluation:dev`) and three Deployments, each overriding the
container's `command` to start a different process. The `beat` Deployment
is hardcoded to exactly 1 replica in its template (not configurable via
values) because running more than one Celery beat instance causes the
scheduled task to fire multiple times per interval — that's a Celery
constraint, not a style choice.

**Why monolith-service and worker-service share a base image.**
Both pull from the exact same root `requirements.txt` (confirmed: every
sub-service's own `requirements.txt` just does `-r ../../requirements.txt`).
`Dockerfile.base` installs that dependency set ONE time; `Dockerfile.monolith`
and `Dockerfile.worker` both build `FROM rag-base:dev` and only add their
own code plus a different `CMD`. This means the expensive PyTorch/PaddleOCR/
Surya/GLiNER install layer is built once and reused — not duplicated across
two multi-gigabyte images.

**Secrets vs ConfigMaps.** Every Deployment template uses `envFrom` with
two sources: a `ConfigMapRef` (non-secret values — ports, feature flags,
queue names) and a `SecretRef` (API keys, DB credentials, JWT material).
The ConfigMap is rendered by this chart from `values.yaml`. The Secret is
NOT rendered by this chart — it must already exist in the cluster
(see Prerequisites step 3) before `helm install` runs, otherwise pods will
be stuck in `CreateContainerConfigError`.

**worker-service has no Service or HPA.** It's a Celery consumer with no
HTTP server — there's nothing to route traffic to and no `/metrics` HTTP
endpoint to probe locally (Celery task metrics are exposed indirectly,
through `celery_metrics.py`'s Prometheus counters being scraped via the
monolith's merged `/metrics` route). Scaling it is a manual `replicaCount`
change per environment file, not CPU-based autoscaling, since CPU usage
doesn't map cleanly to Celery queue backlog.

**External services (Postgres, Redis, Keycloak, Qdrant).** Qdrant runs
embedded inside whichever process imports `retrieval-service`'s code (the
monolith) — no separate container or chart entry needed for it. Postgres,
Redis, and Keycloak are NOT deployed by this chart at all; `values.yaml`'s
`externalServices` block just documents the expected host/port so you can
wire them into the Secret/ConfigMap data manually. If you later want Helm
to deploy these too (e.g. via Bitnami subcharts), that's a deliberate,
separate decision — not something this chart does implicitly.

**Why rag-ui's nginx proxy target isn't hardcoded.** `rag-ui`'s Deployment
sets `MONOLITH_SERVICE_HOST` to `{{ .Release.Name }}-monolith-service` —
the *rendered* Service name, not a guess. Since `monolith-service` and
`rag-ui` are both subcharts of the same umbrella install, they always
share the same `.Release.Name`, so this resolves correctly no matter what
you name the release (`helm install rag-platform ...` vs
`helm install my-test-release ...`). The container's `CMD` runs `envsubst`
on `nginx.conf` at startup to substitute that value in — nginx itself
never sees a Helm template, just a plain config file with the real
hostname already filled in.

This DOES assume both Services land in the **same Kubernetes namespace**
(true for a plain `helm install` with no `--namespace` override, which is
what every example in this README uses). If you ever install this chart
across multiple namespaces, `MONOLITH_SERVICE_HOST` would need the fully
qualified form (`<service>.<namespace>.svc.cluster.local`) instead — not
needed for the current single-namespace setup, but worth knowing if that
changes later.

**Why rag-ui is the only externally-facing Service, and what that means
for TLS.** The browser only ever talks to `rag-ui`; `monolith-service`,
`evaluation-service`, and `worker-service` are all `ClusterIP`-only and
unreachable from outside the cluster. This is *why* the Ingress + TLS
certificate (a teammate's separate task) only needs to target one Service
— `{{ .Release.Name }}-rag-ui`, port 80 — not four. Traffic from `rag-ui`'s
nginx to `monolith-service` stays plain HTTP inside the cluster network,
which is normal; that's not a public-facing hop, so it doesn't need its
own certificate.
