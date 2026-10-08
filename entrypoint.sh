#!/bin/sh
set -eu
python manage.py migrate --noinput
python manage.py seed_providers
python manage.py create_admin
python manage.py collectstatic --noinput
exec gunicorn bill_tracker.wsgi:application --bind 0.0.0.0:8000 --workers 2 --timeout 60 --no-control-socket

