#!/bin/sh
# Create a manual backup now and print its file name (AWS trial, docs/deploy/aws.md).
# Run from the repository directory on the server. Contains no secrets.
set -eu
cd "$(dirname -- "$0")/.."
docker compose exec -T api python -m leadworkbench.admin backup
