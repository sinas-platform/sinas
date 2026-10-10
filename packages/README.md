# Packages

Sinas packages shipped with the platform. Each file is a `kind: SinasPackage`
document you can install as is:

```bash
curl -X POST $SINAS_URL/api/v1/packages/install \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d "{\"source\": $(jq -Rs . < packages/<name>.yaml)}"
```

| Package | What it installs |
|---|---|
| [`way-of-working.yaml`](way-of-working.yaml) | A preloadable "way of working" skill that makes an agent work like Claude Code (files in the workbench, verification by code, references instead of pasted content, checkout/promote, questions only when blocked) and an exemplar agent wired for the full loop. See [Way of Working](../docs-mint/build-resources/way-of-working.mdx). |

Packages here are covered by the backend unit tests (`backend/tests/unit/test_way_of_working_*.py`): they must apply cleanly through the package installer.
