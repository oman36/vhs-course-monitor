#!/bin/bash -l
CUR_DIR=$(dirname $(realpath $0))
cd $CUR_DIR
uv run main.py >> $CUR_DIR/exec.log 2>&1
