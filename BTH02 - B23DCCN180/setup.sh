#!/usr/bin/env bash
cd "$(dirname "$0")"
python3 -m venv .venv && source .venv/bin/activate
pip install -U pip && pip install -r requirements.txt
[ -f .env ] || cp .env.example .env
echo "Xong. Sua .env (token InfluxDB) roi chay: ./run.sh check"
