#!/bin/bash
export TMPDIR="${XDG_RUNTIME_DIR}/app/${FLATPAK_ID}"

FLAGS=()

if [[ -f "${XDG_CONFIG_HOME}/qq-flags.conf" ]]; then
  echo "Reading user defined flags from ${XDG_CONFIG_HOME}/qq-flags.conf..."
  readarray -t FLAGS < "${XDG_CONFIG_HOME}/qq-flags.conf"
elif [[ -n "${WAYLAND_DISPLAY:-}" ]]; then
  echo "Adding default Wayland flags..."
  FLAGS+=(--enable-wayland-ime --wayland-text-input-version=3 --ozone-platform=wayland)
else
  echo "Adding default X11 flags..."
  FLAGS+=(--ozone-platform=x11)
fi

echo "Pass \`${FLAGS[*]}\` to main process..."

if [[ -n "$(ls -A /tmp/.X11-unix 2>/dev/null)" ]]; then
  exec zypak-wrapper /app/extra/QQ/qq "${FLAGS[@]}" "$@"
else
  echo "X11 socket is not available, using Wayland + Xvfb..."
  exec xvfb-run -a zypak-wrapper /app/extra/QQ/qq "${FLAGS[@]}" "$@"
fi
