#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
demo_results="$project_dir/frontend/test-results"
demo_output="$project_dir/docs/assets/agent-demo.gif"

cd "$project_dir"
DEMO_CAPTURE=1 npm --prefix frontend run test:e2e -- demo.spec.ts

demo_video="$(find "$demo_results" -name video.webm -type f -print -quit)"
if [[ -z "$demo_video" ]]; then
  echo "Playwright did not produce video.webm" >&2
  exit 1
fi

ffmpeg -y -ss 2 -i "$demo_video" \
  -vf "fps=8,scale=960:-1:flags=lanczos,split[s0][s1];[s0]palettegen=max_colors=128[p];[s1][p]paletteuse=dither=bayer" \
  "$demo_output"

echo "Demo written to $demo_output"
