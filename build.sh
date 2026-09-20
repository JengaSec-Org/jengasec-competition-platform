#!/usr/bin/env bash
# Render build step. Runs on every deploy, before the new instance starts.
#   Build Command:  ./build.sh
#   Start Command:  gunicorn config.wsgi:application --bind 0.0.0.0:$PORT
set -o errexit

pip install --upgrade pip
pip install -r requirements.txt

python manage.py collectstatic --noinput
python manage.py migrate --noinput
python manage.py ensure_superuser
