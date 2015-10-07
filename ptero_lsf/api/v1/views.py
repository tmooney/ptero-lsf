from . import validators
from flask import g, request, url_for
from flask.ext.restful import Resource
from ptero_common.logging_configuration import logged_response
import logging
import uuid


LOG = logging.getLogger(__name__)


class JobListView(Resource):
    def post(self):
        job_id = str(uuid.uuid4())
        LOG.info("Handling POST request to %s from %s for job (%s)",
                request.url, request.access_route[0], job_id)
        try:
            LOG.debug("Validating JSON body of request for job (%s)", job_id)
            data = validators.get_job_post_data()
        except Exception as e:
            LOG.exception(e)
            LOG.info("Returning 400 in response to request for job (%s)",
                    job_id)
            return {'error': e.message}, 400

        data['job_id'] = job_id
        job_id, job_data = g.backend.create_job(**data)

        LOG.info("Returning 201 in response to request for job (%s)",
                job_id)
        return {'jobId': job_id}, 201, {'Location': url_for('job', pk=job_id)}


class JobView(Resource):
    @logged_response(logger=LOG)
    def get(self, pk):
        job_data = g.backend.get_job(pk)
        if job_data:
            return job_data, 200
        else:
            return None, 404
