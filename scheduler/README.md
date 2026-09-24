# borneosky-scheduler

Starts the `Ingest` workflow every hour at :23 (UTC) via the GitHub API.

Needs one secret, `GITHUB_TOKEN`: a fine-grained personal access token limited to
this repository with **Actions: Read and write**. Nothing else.
