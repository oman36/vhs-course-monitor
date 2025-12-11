#!/bin/bash
DIR='/root/vhs-course-monitor'
HOST='serv'
USER_NAME='root'
ssh "${USER_NAME}@${HOST}" -T <<EOF
find "$DIR" -name "__pycache__" -exec rm -r {} +
EOF
rsync . "${USER_NAME}@${HOST}:${DIR}" \
  --recursive \
  --delete \
  --update \
  --force \
  --links \
  --exclude .git \
  --exclude .idea \
  --exclude .vscode \
  --exclude .venv \
  --exclude .DS_Store \
  --exclude __pycache__ \
  --exclude "*.log" \
  --exclude .ruff_cache \
  --exclude course_codes.txt \
  --exclude "*.html" \
  --verbose
