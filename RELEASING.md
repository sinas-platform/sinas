# Releasing Sinas

Container images for every version, release candidate, and (on demand) any feature branch
are built and pushed to GHCR by [`.github/workflows/build-images.yml`](.github/workflows/build-images.yml).

Images: `ghcr.io/sinas-platform/sinas/{backend,builder,executor,console}`.

## Versioning

- **Final releases:** `0.3.0` (plain semver, no `v` prefix).
- **Release candidates:** `0.3.0-rc.1`, `0.3.0-rc.2`, …
- The version literal lives in **one place**: `backend/app/_version.py`. Bump it there and
  nowhere else.

## What gets built, and the tags produced

| Trigger | Image tags | Moves `:latest`? |
| --- | --- | --- |
| push to `dev` | `:dev`, `:sha-<sha>` | no |
| push to `main` | `:edge`, `:sha-<sha>` | no |
| tag `0.3.0-rc.1` | `:0.3.0-rc.1` | no (prerelease) |
| tag `0.3.0` | `:0.3.0`, `:0.3`, `:latest` | **yes** |
| manual dispatch | `:<image_tag>` (or sanitized ref name) | no |

Only a **final** semver tag updates `:latest` — release candidates never do.

## Cutting a release (with RCs)

```bash
# 1. Freeze: branch off dev once all the release's features have landed there.
git checkout dev && git pull
git checkout -b release/0.3.0

# 2. Bump the version (single source of truth).
#    edit backend/app/_version.py -> __version__ = "0.3.0"
git commit -am "chore(release): 0.3.0"
git push -u origin release/0.3.0

# 3. Publish a release candidate. This builds :0.3.0-rc.1 images and a
#    PRE-release on GitHub with a pinned docker-compose attached.
git tag 0.3.0-rc.1 && git push origin 0.3.0-rc.1
#    -> testers re-run the installer with SINAS_VERSION=0.3.0-rc.1, or
#       `git checkout 0.3.0-rc.1 && docker compose pull && docker compose up -d`
#       (files and images together; never IMAGE_TAG alone)
#    Fixes land on release/0.3.0; tag 0.3.0-rc.2, etc., until green.

# 4. Ship. Merge the release branch to main and tag the final version.
#    (open a PR release/0.3.0 -> main, merge, then:)
git checkout main && git pull
git tag 0.3.0 && git push origin 0.3.0
#    -> builds :0.3.0 :0.3 :latest and a GitHub Release with pinned compose.

# 5. Back-merge so main's release commit(s) return to dev.
git checkout dev && git merge main && git push
```

## Release artifacts

Every version tag publishes, besides the images:

- a version-pinned **`docker-compose-<version>.yml`** (GitHub Release asset)
- the packaged **Helm chart `sinas-<version>.tgz`** (GitHub Release asset), with chart
  version and appVersion stamped from the tag
- the same chart pushed to **`oci://ghcr.io/sinas-platform/charts/sinas`**, so users can
  `helm install --version <version>` with no checkout

The OCI push runs after the Release is created and is non-fatal — the attached `.tgz` is
the guaranteed install path.

## Running a specific version

Every release attaches a **version-pinned `docker-compose-<version>.yml`** to its GitHub
Release — download it and:

```bash
docker compose -f docker-compose-0.3.0.yml up -d   # provide your own .env
```

Or, from a repo checkout, check out the same version first: the compose files,
`Caddyfile` and images change together between versions (0.5.0 moved the console
to port 8080 and runs the images as non-root), so setting `IMAGE_TAG` alone in an
older checkout runs new images against old files. Tracking `:latest` (a plain
`docker compose pull`, or an auto-updater such as Watchtower) has the same problem.

```bash
git checkout 0.3.0                            # or 0.3.0-rc.1, dev, …
IMAGE_TAG=0.3.0 docker compose pull && IMAGE_TAG=0.3.0 docker compose up -d
```

For installs made with `install.sh`, re-run it with `SINAS_VERSION=<version>`.

## Building a feature branch on demand (no merge needed)

To test a branch's images before merging (e.g. the k8s work):

1. **Actions → Build & Push Docker Images → Run workflow**
2. **ref:** the branch/tag/SHA (e.g. `69-helm-chart-for-deploying-sinas-on-k8s`)
3. **image_tag:** an optional short name (e.g. `k8s-test`); omit to use a sanitized ref name

Then consume the images:

```bash
git checkout <that branch>                         # files matching the images
IMAGE_TAG=k8s-test docker compose up -d            # docker-compose
helm upgrade --install sinas charts/sinas \
  --set image.tag=k8s-test --set executor.image=ghcr.io/sinas-platform/sinas/executor:k8s-test
```

Dispatching with an explicit **ref** input runs the current workflow from `dev`/`main` while
building the target ref, so you can build a branch even if its own copy of the workflow is
out of date.

## Notes

- **Multi-arch:** images are built for `linux/amd64` + `linux/arm64`. The arm64 build uses
  QEMU emulation, which adds wall-clock time. If CI gets slow, switch arm64 to a native
  `ubuntu-24.04-arm` runner and merge manifests (matrix over platform), or restrict
  multi-arch to release tags only.
- Merges to `dev`/`main` may require `--admin` to bypass the branch ruleset; that shows as
  `mergeStateStatus: BLOCKED`, not a real conflict.
