import celery
from ptero_common import nicer_logging


LOG = nicer_logging.getLogger(__name__)


__all__ = ['UpdateJobStatus']


class UpdateJobStatus(celery.Task):
    ignore_result = True

    def run(self, job_id):
        LOG.debug('Updating job status for job (%s)', job_id,
                extra={'jobId': job_id})
        try:
            backend = celery.current_app.factory.create_backend()
            backend.update_job_with_lsf_status(job_id)
        except:
            LOG.exception(
                "Exception while trying to update status for job (%s)",
                job_id, extra={'jobId': job_id})
            raise
        finally:
            if backend is not None:
                backend.cleanup()
