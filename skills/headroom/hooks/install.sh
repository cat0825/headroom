#!/bin/sh
# macOS/Linux wrapper for install_hooks.py: finds Python 3.10+ and passes all
# options through (e.g. --dry-run, --uninstall, --display desktop, --link-skill).
# Set PYTHON=/path/to/python3 to choose the interpreter the hooks will use.
set -eu
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [ -n "${PYTHON:-}" ]; then
  if command -v "$PYTHON" >/dev/null 2>&1 &&
     "$PYTHON" -c 'import sys; sys.exit(sys.version_info < (3, 10))' >/dev/null 2>&1; then
    exec "$PYTHON" "$here/install_hooks.py" "$@"
  fi
  echo "headroom: PYTHON must point to a working Python 3.10+: $PYTHON" >&2
  exit 1
fi
for candidate in python3.14 python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$candidate" >/dev/null 2>&1 &&
     "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 10))' >/dev/null 2>&1; then
    exec "$candidate" "$here/install_hooks.py" "$@"
  fi
done
echo "headroom: Python 3.10+ is required (macOS's /usr/bin/python3 may be older)." >&2
echo "Install it from python.org or with 'brew install python', then re-run." >&2
exit 1
