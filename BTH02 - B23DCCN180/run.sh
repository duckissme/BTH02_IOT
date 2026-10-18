#!/usr/bin/env bash
cd "$(dirname "$0")"; source .venv/bin/activate
case "$1" in
  check) python check_env.py;;
  collector) python -m src.collector;;
  app) streamlit run app/app.py;;
  preprocess) python -m src.preprocess --source influx --start -2h --write-influx --report data/report.json;;
  simulate) python -m src.simulate_publisher --host broker.emqx.io --minutes 10 --interval 2;;
  demo) python -m src.simulate_publisher --csv data/sim_raw.csv --minutes 120 && streamlit run app/app.py;;
  *) echo "Dung: ./run.sh check|collector|app|preprocess|simulate|demo";;
esac
