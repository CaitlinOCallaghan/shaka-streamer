#!/bin/bash

# This is a script to easily run Shaka Streamer in transmux-only mode from the command line. 
../shaka-streamer \
  --skip_deps_check \
  -i ../config_files/input_live_transmux_only.yaml \
  -p ../config_files/pipeline_live_transmux_only.yaml \
  -o ../output/live_transmux_only