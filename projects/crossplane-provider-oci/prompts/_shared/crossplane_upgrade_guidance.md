## Crossplane Provider OCI Upgrade Guidance

You are working on a Terraform provider version upgrade for Crossplane Provider
OCI. Keep the change scoped to the provider version bump, generated fallout, and
owner-facing evidence needed to review that bump.

The source-of-truth anchor is `TERRAFORM_PROVIDER_VERSION` in `Makefile`.
Generated files are derived output; do not hand-edit them unless repository
evidence shows a source-of-truth gap that generation cannot fix.

Inspect these surfaces when relevant:

- `Makefile`
- `config/`
- `apis/`
- `internal/apis/`
- `internal/controller/`
- `package/crds/`
- `examples-generated/`
- `docs/`
- `.github/workflows/`

Do not publish artifacts, mutate Kubernetes or OKE clusters, run live OCI
operations, or use secrets unless an approval gate explicitly allows that
action. Do not include raw credentials, kubeconfigs, tokens, tenancy OCIDs, or
other sensitive values in the response.

If the workspace does not look like the Crossplane Provider OCI repository,
return `FAILED` with a clear diagnostic rather than guessing.
