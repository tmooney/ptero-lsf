web: gunicorn ptero_lsf.api.wsgi:app --access-logfile - --error-logfile - --bind 0.0.0.0:$PTERO_LSF_PORT -w 3
poller: celery worker -A ptero_lsf.implementation.celery_app -Q poll --concurrency 1 -n poller$PORT.%h
updater: celery worker -A ptero_lsf.implementation.celery_app -Q update --concurrency 1 -n updater$PORT.%h
worker:  celery worker -A ptero_lsf.implementation.celery_app -Q lsftask --concurrency 1 -n worker$PORT.%h
http_worker: celery worker -A ptero_lsf.implementation.celery_app -Q http --concurrency 1 -n http_worker$PORT.%h
scheduler: celery beat -A ptero_lsf.implementation.celery_app
