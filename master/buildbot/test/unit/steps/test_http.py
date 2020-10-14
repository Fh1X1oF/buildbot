# This file is part of Buildbot.  Buildbot is free software: you can
# redistribute it and/or modify it under the terms of the GNU General Public
# License as published by the Free Software Foundation, version 2.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE.  See the GNU General Public License for more
# details.
#
# You should have received a copy of the GNU General Public License along with
# this program; if not, write to the Free Software Foundation, Inc., 51
# Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.
#
# Copyright Buildbot Team Members

import mock

from twisted.internet import defer
from twisted.internet import reactor
from twisted.trial import unittest
from twisted.web.resource import Resource
from twisted.web.server import Site
from twisted.web.util import redirectTo

from buildbot.process import properties
from buildbot.process.results import FAILURE
from buildbot.process.results import SUCCESS
from buildbot.steps import http
from buildbot.test.util import steps
from buildbot.test.util.misc import TestReactorMixin

try:
    import txrequests
    assert txrequests
    import requests
    assert requests
except ImportError:
    txrequests = requests = None


# We use twisted's internal webserver instead of mocking requests
# to be sure we use the correct requests interfaces

class TestPage(Resource):
    isLeaf = True

    def render_GET(self, request):
        if request.uri == b"/404":
            request.setResponseCode(404)
            return b"404"
        elif request.uri == b'/redirect':
            return redirectTo(b'/redirected-path', request)
        elif request.uri == b"/header":
            return b"".join(request.requestHeaders.getRawHeaders(b"X-Test"))
        return b"OK"

    def render_POST(self, request):
        if request.uri == b"/404":
            request.setResponseCode(404)
            return b"404"
        return b"OK:" + request.content.read()


class TestHTTPStep(steps.BuildStepMixin, TestReactorMixin, unittest.TestCase):

    timeout = 3  # those tests should not run long

    def setUp(self):
        self.setUpTestReactor()
        if txrequests is None:
            raise unittest.SkipTest(
                "Need to install txrequests to test http steps")

        # ignore 'http_proxy' environment variable when running tests
        session = http.getSession()
        session.trust_env = False

        # port 0 means random unused port
        self.listener = reactor.listenTCP(0, Site(TestPage()))
        self.port = self.listener.getHost().port
        return self.setUpBuildStep()

    @defer.inlineCallbacks
    def tearDown(self):
        http.closeSession()
        try:
            yield self.listener.stopListening()
        finally:
            yield self.tearDownBuildStep()

    def get_connection_string(self):
        return "http://127.0.0.1:{}".format(self.port)

    def getURL(self, path=""):
        return '{}/{}'.format(self.get_connection_string(), path)

    def test_get(self):
        url = self.getURL()
        self.setupStep(http.GETNewStyle(url))
        self.expectLogfile('log', "URL: {}\nStatus: 200\n ------ Content ------\nOK".format(url))
        self.expectLogfile('content', "OK")
        self.expectOutcome(result=SUCCESS, state_string="Status code: 200")
        return self.runStep()

    def test_connection_error(self):
        def throwing_request(*args, **kwargs):
            raise requests.exceptions.ConnectionError("failed to connect")

        with mock.patch.object(http.getSession(), 'request', throwing_request):
            url = self.getURL("path")
            self.setupStep(http.GETNewStyle(url))
            self.expectOutcome(result=FAILURE, state_string="Requested (failure)")
            return self.runStep()

    def test_redirect(self):
        url = self.getURL("redirect")
        self.setupStep(http.GETNewStyle(url))

        expected_log = '''
Redirected 1 times:

URL: {0}/redirect
 ------ Content ------

<html>
    <head>
        <meta http-equiv="refresh" content="0;URL=/redirected-path">
    </head>
    <body bgcolor="#FFFFFF" text="#000000">
    <a href="/redirected-path">click here</a>
    </body>
</html>
============================================================
URL: {0}/redirected-path
Status: 200
 ------ Content ------
OK'''.format(self.get_connection_string())

        self.expectLogfile('log', expected_log)
        self.expectLogfile('content', "OK")
        self.expectOutcome(result=SUCCESS, state_string="Status code: 200")
        return self.runStep()

    def test_404(self):
        url = self.getURL("404")
        self.setupStep(http.GETNewStyle(url))
        self.expectLogfile('log', "URL: {}\n ------ Content ------\n404".format(url))
        self.expectLogfile('content', "404")
        self.expectOutcome(result=FAILURE, state_string="Status code: 404 (failure)")
        return self.runStep()

    def test_method_not_allowed(self):
        url = self.getURL("path")
        self.setupStep(http.PUTNewStyle(url))
        self.expectOutcome(result=FAILURE, state_string="Status code: 501 (failure)")
        return self.runStep()

    def test_post(self):
        url = self.getURL("path")
        self.setupStep(http.POSTNewStyle(url))
        self.expectOutcome(result=SUCCESS, state_string="Status code: 200")
        self.expectLogfile('log', "URL: {}\nStatus: 200\n ------ Content ------\nOK:".format(url))
        self.expectLogfile('content', "OK:")
        return self.runStep()

    def test_post_data(self):
        url = self.getURL("path")
        self.setupStep(http.POSTNewStyle(url, data='mydata'))
        self.expectOutcome(result=SUCCESS, state_string="Status code: 200")
        self.expectLogfile('log',
                           "URL: {}\nStatus: 200\n ------ Content ------\nOK:mydata".format(url))
        self.expectLogfile('content', "OK:mydata")
        return self.runStep()

    def test_post_data_dict(self):
        url = self.getURL("path")

        self.setupStep(http.POSTNewStyle(url, data={'key1': 'value1'}))
        self.expectOutcome(result=SUCCESS, state_string="Status code: 200")
        self.expectLogfile('log', '''\
URL: {}
Status: 200
 ------ Content ------
OK:key1=value1'''.format(url))
        self.expectLogfile('content', "OK:key1=value1")
        return self.runStep()

    def test_header(self):
        url = self.getURL("header")
        self.setupStep(http.GETNewStyle(url, headers={"X-Test": "True"}))
        self.expectLogfile('log', "URL: {}\nStatus: 200\n ------ Content ------\nTrue".format(url))
        self.expectOutcome(result=SUCCESS, state_string="Status code: 200")
        return self.runStep()

    def test_params_renderable(self):
        url = self.getURL()
        self.setupStep(http.GETNewStyle(url, params=properties.Property("x")))
        self.properties.setProperty(
            'x', {'param_1': 'param_1', 'param_2': 2}, 'here')
        self.expectLogfile('log',
            ("URL: {}?param_1=param_1&param_2=2\nStatus: 200\n ------ Content ------\nOK"
             ).format(url))
        self.expectLogfile('content', "OK")
        self.expectOutcome(result=SUCCESS, state_string="Status code: 200")
        return self.runStep()
