#!/usr/bin/env bash
# Double-click this file in Finder to start localdeck.
# It opens a Terminal window, runs the dashboard, and shows live logs.
# Press Ctrl+C in that window (or just close it) to stop.

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"
exec ./launch.sh
