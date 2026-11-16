#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -lt 3 ]; then
  echo "usage: publish_release.sh <tag> <title> <file>..." >&2
  exit 2
fi

tag="$1"
title="$2"
shift 2

for f in "$@"; do
  if [ ! -s "$f" ]; then
    echo "missing or empty file: $f" >&2
    exit 1
  fi
done

if ! gh release view "$tag" >/dev/null 2>&1; then
  gh release create "$tag" \
    --title "$title" \
    --notes "Automatically published data files. Assets are replaced in place." \
    --latest=false
fi

gh release upload "$tag" "$@" --clobber
