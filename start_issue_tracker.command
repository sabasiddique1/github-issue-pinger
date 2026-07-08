#!/bin/bash
set -euo pipefail

cd "$(dirname "$0")"
source .venv/bin/activate
python github_issue_tracking_server.py
