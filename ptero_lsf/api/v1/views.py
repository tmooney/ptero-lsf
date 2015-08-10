from . import validators
from flask import g, url_for
from flask.ext.restful import Resource
from ptero_common.logging_configuration import logged_response
import logging


LOG = logging.getLogger(__name__)


class JobListView(Resource):
    @logged_response(logger=LOG)
    def post(self):
        try:
            data = validators.get_job_post_data()
        except Exception as e:
            LOG.exception(e)
            return {'error': e.message}, 400

        job_id, job_data = g.backend.create_job(**data)

        return job_data, 201, {'Location': url_for('job', pk=job_id)}


class JobView(Resource):
    @logged_response(logger=LOG)
    def get(self, pk):
        job_data = g.backend.get_job(pk)
        if job_data:
            return job_data, 200
        else:
            return None, 404
