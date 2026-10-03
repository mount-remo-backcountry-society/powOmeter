#!/bin/sh
# Install the local pre-commit hook (run once per clone, from the repo root):
#   sh tools/install-hooks.sh
# The hook runs the privacy check on everything being committed, INCLUDING the
# home-coordinate check, which needs .private/home_coords.txt (never committed;
# one "lat,lon" per line, e.g. 54.5,-128.6).
set -e
hook=".git/hooks/pre-commit"
cat > "$hook" <<'EOF'
#!/bin/sh
python tools/privacy_check.py --staged --local || {
  echo "Commit blocked by the privacy check (see above)."
  exit 1
}
EOF
chmod +x "$hook"
echo "Installed $hook"
[ -f .private/home_coords.txt ] || echo "Reminder: create .private/home_coords.txt (lat,lon) for the home-location check."
