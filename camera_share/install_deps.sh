#!/usr/bin/env bash
set -euo pipefail

export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:$PATH"

KERNEL_RELEASE="$(uname -r)"

usage() {
  cat <<EOF
Usage: $(basename "$0")

Installs camera sharing dependencies on Linux:
  - v4l2loopback-dkms
  - v4l-utils
  - ffmpeg
  - matching kernel headers for DKMS

Supported distributions:
  - Debian
  - Ubuntu
  - Raspberry Pi OS
EOF
}

die() {
  echo "ERROR: $*" >&2
  exit 1
}

command_exists() {
  command -v "$1" >/dev/null 2>&1
}

run_root() {
  if [[ "$EUID" -eq 0 ]]; then
    "$@"
  else
    command_exists sudo || die "sudo is required when not running as root."
    sudo "$@"
  fi
}

headers_installed() {
  [[ -e "/lib/modules/$KERNEL_RELEASE/build" || -e "/usr/lib/modules/$KERNEL_RELEASE/build" ]]
}

install_apt() {
  local header_pkg="linux-headers-$KERNEL_RELEASE"

  echo "Updating apt package index ..."
  run_root apt-get update

  if headers_installed; then
    echo "Kernel headers already installed for $KERNEL_RELEASE."
  elif apt-cache show "$header_pkg" >/dev/null 2>&1; then
    echo "Installing kernel headers: $header_pkg"
    run_root apt-get install -y "$header_pkg"
  elif apt-cache show raspberrypi-kernel-headers >/dev/null 2>&1; then
    echo "Installing Raspberry Pi kernel headers ..."
    run_root apt-get install -y raspberrypi-kernel-headers
  else
    die "Could not find kernel headers for $KERNEL_RELEASE. Install matching kernel headers and rerun this script."
  fi

  echo "Installing v4l2loopback, v4l-utils, and ffmpeg ..."
  run_root apt-get install -y v4l2loopback-dkms v4l-utils ffmpeg
}

main() {
  if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    usage
    exit 0
  fi

  [[ "$(uname -s)" == "Linux" ]] || die "This installer is Linux-only."

  if command_exists apt-get; then
    install_apt
  else
    die "Unsupported Linux distribution. This installer only supports Debian, Ubuntu, and Raspberry Pi OS."
  fi

  echo "Done."
}

main "$@"
