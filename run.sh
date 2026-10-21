#!/usr/bin/env bash
# Run the Streamlit dashboard
set -e
cd "$(dirname "$0")"
pip install -r requirements.txt --quiet
streamlit run app.py
