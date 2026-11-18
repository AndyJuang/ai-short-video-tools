#!/usr/bin/env bash
# Build dist/ad-video-tools.plugin (a plain zip of the committed plugin folder + the repo LICENSE) for Claude Desktop.
# Plain `zip -X -D`, not `git archive --format=zip`: no comment header, no directory entries.
set -euo pipefail
cd "$(dirname "$0")"
out="$PWD/dist/ad-video-tools.plugin"
mkdir -p dist && rm -f "$out"
tmp=$(mktemp -d)
root=$(git rev-parse --show-toplevel)
git -C "$root" archive HEAD -- plugins/ad-video-tools/.claude-plugin plugins/ad-video-tools/skills \
  plugins/ad-video-tools/README.md LICENSE | tar -x -C "$tmp"
mv "$tmp/LICENSE" "$tmp/plugins/ad-video-tools/"
(cd "$tmp/plugins/ad-video-tools" && zip -q -r -y -X -D "$out" .claude-plugin skills README.md LICENSE)
rm -rf "$tmp"
echo "$out"
