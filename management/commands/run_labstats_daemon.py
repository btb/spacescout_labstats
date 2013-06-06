"""
This provides a managament command to run a daemon that will frequently update
the spots that have corresponding labstats information with the number of
machines available and similar information.
"""
from django.core.management.base import BaseCommand
from spacescout_labstats.utils import upload_data
from django.conf import settings
from optparse import make_option
from datetime import datetime
from SOAPpy import WSDL
import os
import sys
import time
import atexit
import oauth2
import json
import logging

logging.basicConfig()
logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'This updates spots with labstats data'

    option_list = BaseCommand.option_list + (
        make_option('--daemonize',
                    dest='daemon',
                    default=True,
                    action='store_true',
                    help='This will set the updater to run as a daemon.'),
        make_option('--update-delay',
                    dest='update_delay',
                    type='float',
                    default=5,
                    help='The number of minutes between update attempts.'),
        make_option('--run-once',
                    dest='run_once',
                    default=False,
                    action='store_true',
                    help='This will allow the updater to run just once.'),
    )

    def handle(self, *args, **options):
        """
        This is the entry point for the management command. It will handle
        daemonizing the script as needed.
        """

        atexit.register(self.remove_pid_file)

        daemon = options["daemon"]

        if daemon:
            logger.debug("starting the updater as a daemon")
            pid = os.fork()
            if pid == 0:
                os.setsid()

                pid = os.fork()
                if pid != 0:
                    os._exit(0)

            else:
                os._exit(0)

            self.create_pid_file()
            try:
                self.controller(options["update_delay"], options["run_once"])
            except Exception as ex:
                logger.error("Error running the controller: %s", str(ex))

        else:
            logger.debug("starting the updater as an interactive process")
            self.create_pid_file()
            self.controller(options["update_delay"], options["run_once"])

    def controller(self, update_delay, run_once=False):
        """
        This is responsible for the workflow of orchestrating
        the updater process.
        """
        if not hasattr(settings, 'SS_WEB_SERVER_HOST'):
            raise(Exception("Required setting missing: SS_WEB_SERVER_HOST"))
        consumer = oauth2.Consumer(key=settings.SS_WEB_OAUTH_KEY, secret=settings.SS_WEB_OAUTH_SECRET)
        client = oauth2.Client(consumer)

        while True:
            if self.should_stop():
                sys.exit()

            # This allows for a one time run via interactive process for automated testing
            if run_once:
                self.create_stop_file()

            upload_spaces = []

            try:
                url = "%s/api/v1/spot/?extended_info:has_labstats=true" % (settings.SS_WEB_SERVER_HOST)
                resp, content = client.request(url, 'GET')
                labstats_spaces = json.loads(content)

                try:
                    # Updates the num_machines_available extended_info field for spots that have corresponding labstats.
                    stats = WSDL.Proxy(settings.LABSTATS_URL)
                    groups = stats.GetGroupedCurrentStats().GroupStat

                    for space in labstats_spaces:
                        try:
                            for g in groups:
                                # Available data fields froms the labstats groups:
                                    # g.groupName g.availableCount g.groupId g.inUseCount g.offCount g.percentInUse g.totalCount g.unavailableCount

                                if space['extended_info']['labstats_id'] == g.groupName:
                                    space['extended_info'].update({
                                        'auto_labstats_available': g.availableCount,
                                        'auto_labstats_total': g.totalCount,
                                        'auto_labstats_off': g.offCount
                                    })

                                    upload_spaces.append({
                                        'data': json.dumps(space),
                                        'id': space['id'],
                                        #'etag': space['etag']
                                    })

                        except Exception as ex:
                            del space['extended_info']['auto_labstats_available']
                            del space['extended_info']['auto_labstats_total']
                            del space['extended_info']['auto_labstats_off']

                            upload_spaces.append({
                                'data': json.dumps(space),
                                'id': space['id'],
                                #'etag': space['etag']
                            })

                            logger.debug("An error occured updating labstats spot %s: %s", (space.name, str(ex)))


                except Exception as ex:
                    for space in labstats_spaces:
                        del space['extended_info']['auto_labstats_available']
                        del space['extended_info']['auto_labstats_total']
                        del space['extended_info']['auto_labstats_off']

                        upload_spaces.append({
                            'data': json.dumps(space),
                            'id': space['id'],
                            #'etag': space['etag']
                        })
                    logger.debug("Error getting labstats stats: %s", str(ex))

            except Exception as ex:
                logger.debug("Error making the get request to the server: %s", str(ex))

            response = upload_data(upload_spaces)

            if not run_once:
                for i in range(update_delay * 60):
                    if self.should_stop():
                        sys.exit()
                    else:
                        time.sleep(1)

            else:
                sys.exit()

    def read_pid_file(self):
        if os.path.isfile(self._get_pid_file_path()):
            return True
        return False

    def create_pid_file(self):
        handle = open(self._get_pid_file_path(), 'w')
        handle.write(str(os.getpid()))
        handle.close()
        return

    def create_stop_file(self):
        handle = open(self._get_stopfile_path(), 'w')
        handle.write(str(os.getpid()))
        handle.close()
        return

    def remove_pid_file(self):
        os.remove(self._get_pid_file_path())

        if os.path.isfile(self._get_stopfile_path()):
            self.remove_stop_file()

    def remove_stop_file(self):
        os.remove(self._get_stopfile_path())

    def _get_pid_file_path(self):
        if not os.path.isdir("/tmp/updater/"):
            os.mkdir("/tmp/updater/", 0700)
        return "/tmp/updater/%s.pid" % (str(os.getpid()))

    def should_stop(self):
        if os.path.isfile(self._get_stopfile_path()):
            self.remove_stop_file()
            return True
        return False

    def _get_stopfile_path(self):
        if not os.path.isdir("/tmp/updater/"):
            os.mkdir("/tmp/updater/", 0700)
        return "/tmp/updater/%s.stop" % (str(os.getpid()))