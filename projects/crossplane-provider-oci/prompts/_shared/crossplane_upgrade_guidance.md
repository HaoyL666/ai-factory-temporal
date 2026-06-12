## Crossplane Provider OCI Upgrade Guidance

You are working on a Terraform provider version upgrade for Crossplane Provider
OCI. Keep the change scoped to the Terraform provider version and generated
fallout needed by that version.

The primary source-of-truth anchor is `TERRAFORM_PROVIDER_VERSION` in
`Makefile`. Treat generated files as derived unless evidence proves a manual
source-of-truth change is required.

Inspect these surfaces when relevant:

- `Makefile`
- `config/schema.json`
- `config/`
- `apis/`
- `internal/controller/`
- `package/crds/`
- `examples-generated/`
- `docs/`
- `.github/workflows/`

Do not publish artifacts, mutate shared clusters, run live OCI operations, or
use secrets unless an approval gate explicitly allows it. Do not include raw
credentials, kubeconfigs, tokens, tenancy OCIDs, or other sensitive values in
the response.

If the workspace does not look like the Crossplane Provider OCI repository,
return `FAILED` with a clear diagnostic rather than guessing.
