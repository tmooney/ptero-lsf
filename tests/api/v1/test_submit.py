from .base import BaseAPITest
import os
import pprint
import time


class SubmitTest(BaseAPITest):
    def test_submit_small_job(self):
        test_data = 'hello small job'
        outfile = self.make_tempfile()
        submit_data = {
            'command': 'echo "%s"' % test_data,
            'options': {
                'outFile': outfile,
            },
        }
        self.set_queue(submit_data)

        response = self.post(self.jobs_url, submit_data)

        pprint.pprint(response.headers)
        pprint.pprint(response.DATA)
        self.assertEqual(response.status_code, 201)

        time.sleep(5)

        status_response = self.get(response.headers['Location'])
        self.assertEqual(status_response.status_code, 200)

        pprint.pprint(status_response.DATA)

        self.assertTrue(_wait_for_file(outfile))

        data = open(outfile).read()
        self.assertRegexpMatches(data, '%s.*' % test_data)

        status_response = self.get(response.headers['Location'])
        self.assertEqual(status_response.status_code, 200)

        pprint.pprint(status_response.DATA)

        for key, value in submit_data.iteritems():
            self.assertEqual(status_response.DATA[key], value)

        self.assertIsInstance(status_response.DATA['lsfJobId'], int)


_MAX_TRIES = 30
_POLLING_INTERVAL = 10
def _wait_for_file(filename):
    for i in xrange(_MAX_TRIES):
        if os.path.isfile(filename):
            return True

        else:
            time.sleep(_POLLING_INTERVAL)

    return False