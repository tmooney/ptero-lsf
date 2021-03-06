from . import celery_tasks  # noqa
from . import models
from billiard import Pipe, Process
from pprint import pformat
from ptero_common import nicer_logging
from ptero_common.server_info import get_server_info
from ptero_common.exceptions import NoSuchEntityError
from ptero_lsf.implementation import statuses
from sqlalchemy import func
import datetime
import os
import re


LOG = nicer_logging.getLogger(__name__)

MAX_FAILS = int(os.environ.get("PTERO_LSF_MAX_FAILED_UPDATE_ATTEMPTS", 5))

try:
    import lsf
    from lsf.exceptions import InvalidJob
except ImportError:
    if int(os.environ.get("PTERO_LSF_NON_WORKER", "0")):
        LOG.info("Failed to import lsf library, this is OK since "
            "this process is not a worker that needs to access lsf")
    else:
        raise


def _collect_lsf_environment():
    regex = re.compile(r'^LSF_.+')
    result = {}
    for key, value in os.environ.iteritems():
        if regex.match(key):
            result[key] = value

    return result


_LSF_ENVIRONMENT_VARIABLES = _collect_lsf_environment()
_JOB_URL_BASE = "http://%s:%s/v1/jobs/" % (
        os.environ['PTERO_LSF_HOST'],
        os.environ['PTERO_LSF_PORT'])


class SubmitError(Exception):
    pass


class Backend(object):
    def __init__(self, session, celery_app, db_revision):
        self.session = session
        self.celery_app = celery_app
        self.db_revision = db_revision

    def cleanup(self):
        self.session.rollback()

    @property
    def celery_submit_lsf_job(self):
        return self.celery_app.tasks[
'ptero_lsf.implementation.celery_tasks.lsf_task.LSFSubmit'
        ]

    @property
    def celery_kill_lsf_job_by_id(self):
        return self.celery_app.tasks[
'ptero_lsf.implementation.celery_tasks.lsf_task.LSFKillByID'
        ]

    def create_job(self, command, job_id, options=None, rLimits=None,
            webhooks=None, pollingInterval=None, cwd='/tmp', environment=None,
            umask=None, user=None):

        polling_interval = datetime.timedelta(
                seconds=self._determine_polling_interval(pollingInterval))

        if umask is not None:
            umask = int(umask, 8)

        if environment is None:
            environment = {}
        environment.update(_LSF_ENVIRONMENT_VARIABLES)
        environment['PTERO_LSF_JOB_URL'] = _JOB_URL_BASE + job_id

        job = models.Job(id=job_id, command=command, options=options,
                rlimits=rLimits, webhooks=webhooks,
                polling_interval=polling_interval, cwd=cwd,
                environment=environment, umask=umask, user=user)
        self.session.add(job)

        LOG.debug("Setting status of job (%s) to 'new'", job.id,
                extra={'jobId': job.id})
        job.set_status(statuses.new)

        LOG.debug("Commiting job (%s) to DB", job.id,
                extra={'jobId': job.id})
        self.session.commit()
        LOG.debug("Job (%s) committed to DB", job.id,
                extra={'jobId': job.id})

        LOG.info("Submitting Celery LSFSubmit task for job (%s)",
                job.id, extra={'jobId': job.id})
        self.celery_submit_lsf_job.delay(job.id)

        return job.as_dict

    @staticmethod
    def _determine_polling_interval(polling_interval):
        if polling_interval is None:
            return int(os.environ.get("PTERO_LSF_DEFAULT_POLLING_INTERVAL",
                900))
        else:
            return polling_interval

    def get_job(self, job_id):
        job = self._get_job(job_id)
        if job:
            return job.as_dict

    def _get_job(self, job_id):
        job = self.session.query(models.Job).get(job_id)

        if job is None:
            raise NoSuchEntityError("Job %s not found" % job_id)
        else:
            return job

    def submit_job_to_lsf(self, job_id):
        job = self._get_job(job_id)

        try:
            lsf_job_id = self._fork_and_submit_job(job)

            job.lsf_job_id = lsf_job_id
            job.set_status(statuses.submitted)
            job.update_poll_after()

        except Exception as e:
            job.set_status(statuses.errored, message=e.message)

        self.session.commit()

        # done after commit in case job was canceled while we were launching it.
        if self.job_is_canceled(job_id):
            self.kill_lsf_job(job.lsf_job_id)

    def _fork_and_submit_job(self, job):
        parent_pipe, child_pipe = Pipe()
        try:
            p = Process(target=self._submit_job_to_lsf,
                        args=(child_pipe, parent_pipe, job,))
            p.start()

        except:
            parent_pipe.close()
            raise
        finally:
            child_pipe.close()

        try:
            p.join()

            result = parent_pipe.recv()
            if isinstance(result, basestring):
                raise SubmitError(result)

        except EOFError:
            raise SubmitError('Unknown exception submitting job')
        finally:
            parent_pipe.close()

        return result

    def _submit_job_to_lsf(self, child_pipe, parent_pipe, job):
        try:
            parent_pipe.close()

            job.set_user_and_groups()

            job.set_environment()

            job.set_cwd()
            job.set_umask()

            LOG.info("Submitting job (%s) to lsf", job.id,
                    extra={'jobId': job.id})
            lsf_job = lsf.submit(str(job.command), options=job.submit_options,
                          rlimits=job.rlimits)
            LOG.info("Job (%s) has lsf id [%s]", job.id, lsf_job.job_id,
                    extra={'jobId': job.id, 'lsfJobId': lsf_job.job_id})
            child_pipe.send(lsf_job.job_id)
        except Exception as e:
            LOG.exception("Error submitting job (%s) to lsf", job.id)
            child_pipe.send(str(e))

        child_pipe.close()

    def update_job_with_lsf_status(self, job_id):
        LOG.debug('Looking up job (%s) in DB', job_id,
                extra={'jobId': job_id})
        service_job = self.session.query(
            models.Job).get(job_id)
        service_job.awaiting_update = False
        LOG.debug('DB says Job (%s) has lsf id [%s]',
                job_id, service_job.lsf_job_id,
                extra={'jobId': job_id, 'lsfJobId': service_job.lsf_job_id})

        if service_job.lsf_job_id is None:
            service_job.set_status(statuses.errored,
                    message='LSF job id for job (%s) is None' % job_id)
            self.session.commit()
            return False

        else:
            LOG.debug("Querying LSF about job (%s) with lsf id [%s]",
                    job_id, service_job.lsf_job_id,
                    extra={'jobId': job_id, 'lsfJobId': service_job.lsf_job_id})
            lsf_job = lsf.get_job(service_job.lsf_job_id, include_exec_info=False)

            try:
                job_data = lsf_job.as_dict
            except InvalidJob:
                # this could happen if you poll too quickly after launching the
                # job, lets allow for retrying a set number of times.
                service_job.failed_update_count += 1

                LOG.exception("Exception occured while converting lsf"
                    " job to dictionary")

                if (service_job.failed_update_count >= MAX_FAILS):
                    service_job.set_status(statuses.errored,
                        message="Failed to update status too many times: %s"
                            % service_job.failed_update_count)

                self.session.commit()
                return False

            LOG.info("Setting status of job (%s) to %s", job_id,
                    pformat(job_data['statuses']),
                    extra={'jobId': job_id, 'lsfJobId': service_job.lsf_job_id})
            service_job.update_status(job_data['statuses'])
            service_job.update_poll_after()

        self.session.commit()
        return True

    def get_job_ids_to_update(self):
        jobs = self.session.query(models.Job).filter(
                (models.Job.poll_after <= func.now()) &
                (models.Job.failed_update_count < MAX_FAILS)
                ).all()

        job_ids_to_update = []
        for job in jobs:
            if job.awaiting_update:
                LOG.error("Awaiting update already set to True for job (%s)",
                        job.id, extra={'jobID': job.id})
            else:
                job_ids_to_update.append(job.id)
                job.awaiting_update = True

        self.session.commit()

        return job_ids_to_update

    def server_info(self):
        result = get_server_info(
                'ptero_lsf.implementation.celery_app')
        result['databaseRevision'] = self.db_revision
        return result

    def update_job(self, job_id, **kwargs):
        job = self._get_job(job_id)

        for key, value in kwargs.items():
            if value is not None:
                LOG.debug('Updating %s for job (%s) to [%s]', key, job.id,
                        value)
                update_fn = getattr(self, "update_job_%s" % key)
                update_fn(job, value)

        self.session.commit()
        return job.as_dict

    def update_job_status(self, job, status):
        if isinstance(status, basestring):
            message = "Updated via PATCH request"
        else:
            # status is a two element list [<status>, <message>]
            message = status[-1]
            status = status[0]

        if status == statuses.canceled:
            self.cancel_job(job.id, message=message)
        else:
            job.set_status(status, message=message)

    def update_job_stdout(self, job, stdout):
        job.stdout = stdout

    def update_job_stderr(self, job, stderr):
        job.stderr = stderr

    def cancel_job(self, job_id, message):
        job = self._get_job(job_id)

        job.set_status(status=statuses.canceled, message=message)
        self.session.commit()

        # done after commit in case job was launched while we were canceling it
        if job.lsf_job_id is not None:
            LOG.debug("Submitting job to Celery to kill LSF job %s",
                    job.lsf_job_id, extra={'jobId': job.id})
            self.celery_kill_lsf_job_by_id.delay(job.id, job.lsf_job_id)

    def delete_job(self, job_id):
        job = self._get_job(job_id)
        if job.lsf_job_id is not None:
            LOG.debug("Submitting job to Celery to kill LSF job %s",
                    job.lsf_job_id, extra={'jobId': job.id})
            self.celery_kill_lsf_job_by_id.delay(job.id, job.lsf_job_id)
        self.session.delete(job)
        self.session.commit()

    def kill_job(self, job_id):
        """ DEPRECATED: Remove with celery_tasks.lsf_task.LSFKill """
        job = self._get_job(job_id)
        return self.kill_lsf_job(job.lsf_job_id)

    def kill_lsf_job(self, lsf_job_id):
        if lsf_job_id is not None:
            return lsf.bindings.kill_job(lsf_job_id)

    def job_is_canceled(self, job_id):
        return self.session.query(models.JobStatusHistory).filter(
                models.JobStatusHistory.job_id == job_id).filter(
                models.JobStatusHistory.status == statuses.canceled).count() > 0
