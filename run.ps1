# Run the Streamlit dashboard (Windows PowerShell)
$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir
pip install -r requirements.txt --quiet
streamlit run app.py
