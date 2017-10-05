# Copyright 2016 Canonical Ltd
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#  http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import argparse
import sys
import tempfile
import subprocess

import mock
import yaml
import unittest

from mock import patch, MagicMock, call

# python-apt is not installed as part of test-requirements but is imported by
# some charmhelpers modules so create a fake import.
sys.modules['apt'] = MagicMock()
sys.modules['apt_pkg'] = MagicMock()

with patch('charmhelpers.contrib.hardening.harden.harden') as mock_dec, \
        patch('lib.swift_utils.register_configs') as configs:
    mock_dec.side_effect = (lambda *dargs, **dkwargs: lambda f:
                            lambda *args, **kwargs: f(*args, **kwargs))
    import actions.actions
    import actions.add_user


class CharmTestCase(unittest.TestCase):

    def setUp(self, obj, patches):
        super(CharmTestCase, self).setUp()
        self.patches = patches
        self.obj = obj
        self.patch_all()

    def patch(self, method):
        _m = mock.patch.object(self.obj, method)
        mocked = _m.start()
        self.addCleanup(_m.stop)
        return mocked

    def patch_all(self):
        for method in self.patches:
            setattr(self, method, self.patch(method))


class PauseTestCase(CharmTestCase):

    def setUp(self):
        super(PauseTestCase, self).setUp(
            actions.actions, ["service_pause", "set_unit_paused",
                              "assess_status"])

        class FakeArgs(object):
            services = ['swift-proxy', 'haproxy', 'memcached', 'apache2']
        self.args = FakeArgs()

    def test_pauses_services(self):
        """Pause action pauses all of the Swift services."""
        pause_calls = []

        def fake_service_pause(svc):
            pause_calls.append(svc)
            return True

        self.service_pause.side_effect = fake_service_pause

        actions.actions.pause(self.args)
        self.assertEqual(
            pause_calls, ['swift-proxy', 'haproxy', 'memcached', 'apache2'])

    def test_bails_out_early_on_error(self):
        """Pause action fails early if there are errors stopping a service."""
        pause_calls = []

        def maybe_kill(svc):
            if svc == "haproxy":
                return False
            else:
                pause_calls.append(svc)
                return True

        self.service_pause.side_effect = maybe_kill
        self.assertRaisesRegexp(
            Exception, "haproxy didn't stop cleanly.",
            actions.actions.pause, self.args)
        self.assertEqual(pause_calls, ["swift-proxy"])

    def test_pause_sets_value(self):
        """Pause action sets the unit-paused value to True."""
        actions.actions.pause(self.args)
        self.set_unit_paused.assert_called_once_with()


class ResumeTestCase(CharmTestCase):

    def setUp(self):
        super(ResumeTestCase, self).setUp(
            actions.actions, ["service_resume", "clear_unit_paused",
                              "assess_status"])

        class FakeArgs(object):
            services = ['swift-proxy', 'haproxy', 'memcached', 'apache2']
        self.args = FakeArgs()

    def test_resumes_services(self):
        """Resume action resumes all of the Swift services."""
        resume_calls = []

        def fake_service_resume(svc):
            resume_calls.append(svc)
            return True

        self.service_resume.side_effect = fake_service_resume
        actions.actions.resume(self.args)
        self.assertEqual(
            resume_calls, ['swift-proxy', 'haproxy', 'memcached', 'apache2'])

    def test_bails_out_early_on_error(self):
        """Resume action fails early if there are errors starting a service."""
        resume_calls = []

        def maybe_kill(svc):
            if svc == "haproxy":
                return False
            else:
                resume_calls.append(svc)
                return True

        self.service_resume.side_effect = maybe_kill
        self.assertRaisesRegexp(
            Exception, "haproxy didn't start cleanly.",
            actions.actions.resume, self.args)
        self.assertEqual(resume_calls, ['swift-proxy'])

    def test_resume_sets_value(self):
        """Resume action sets the unit-paused value to False."""
        actions.actions.resume(self.args)
        self.clear_unit_paused.assert_called_once_with()


class GetActionParserTestCase(unittest.TestCase):

    def test_definition_from_yaml(self):
        """ArgumentParser is seeded from actions.yaml."""
        actions_yaml = tempfile.NamedTemporaryFile(
            prefix="GetActionParserTestCase", suffix="yaml")
        actions_yaml.write(yaml.dump({"foo": {"description": "Foo is bar"}}))
        actions_yaml.seek(0)
        parser = actions.actions.get_action_parser(actions_yaml.name, "foo",
                                                   get_services=lambda: [])
        self.assertEqual(parser.description, 'Foo is bar')


class MainTestCase(CharmTestCase):

    def setUp(self):
        super(MainTestCase, self).setUp(
            actions.actions, ["_get_action_name",
                              "get_action_parser",
                              "action_fail"])

    def test_invokes_pause(self):
        dummy_calls = []

        def dummy_action(args):
            dummy_calls.append(True)

        self._get_action_name.side_effect = lambda: "foo"
        self.get_action_parser = lambda: argparse.ArgumentParser()
        with mock.patch.dict(actions.actions.ACTIONS, {"foo": dummy_action}):
            actions.actions.main([])
        self.assertEqual(dummy_calls, [True])

    def test_unknown_action(self):
        """Unknown actions aren't a traceback."""
        self._get_action_name.side_effect = lambda: "foo"
        self.get_action_parser = lambda: argparse.ArgumentParser()
        exit_string = actions.actions.main([])
        self.assertEqual("Action foo undefined", exit_string)

    def test_failing_action(self):
        """Actions which traceback trigger action_fail() calls."""
        dummy_calls = []

        self.action_fail.side_effect = dummy_calls.append
        self._get_action_name.side_effect = lambda: "foo"

        def dummy_action(args):
            raise ValueError("uh oh")

        self.get_action_parser = lambda: argparse.ArgumentParser()
        with mock.patch.dict(actions.actions.ACTIONS, {"foo": dummy_action}):
            actions.actions.main([])
        self.assertEqual(dummy_calls, ["uh oh"])


class AddUserTestCase(CharmTestCase):

    def setUp(self):
        super(AddUserTestCase, self).setUp(
            actions.add_user, ["action_get", "action_set",
                               "action_fail", "check_call",
                               "try_initialize_swauth", "config",
                               "determine_api_port", "leader_get"])

    def test_success(self):
        """Ensure that the action_set is called on succees."""
        self.config.return_value = "swauth"
        self.action_get.return_value = "test"
        self.determine_api_port.return_value = 8070
        actions.add_user.add_user()
        self.leader_get.assert_called_with("swauth-admin-key")
        calls = [call("account"), call("username"), call("password")]
        self.action_get.assert_has_calls(calls)
        self.action_set.assert_called_once_with({
            'add-user.result': 'Success',
            'add-user.message': "Successfully added the user test",
        })

    def test_failure(self):
        """Ensure that action_fail is called on failure."""
        self.config.return_value = "swauth"
        self.action_get.return_value = "test"
        self.determine_api_port.return_value = 8070
        self.CalledProcessError = ValueError

        self.check_call.side_effect = subprocess.CalledProcessError(0,
                                                                    "hi",
                                                                    "no")
        actions.add_user.add_user()
        self.leader_get.assert_called_with("swauth-admin-key")
        calls = [call("account"), call("username"), call("password")]
        self.action_get.assert_has_calls(calls)
        self.action_set.assert_not_called()

        self.action_fail.assert_called_once_with(
            'Adding user test failed with: ""')


class DiskUsageTestCase(CharmTestCase):

    def setUp(self):
        super(DiskUsageTestCase, self).setUp(
            actions.actions, ["check_output", "action_set", "action_fail"])

    def test_success(self):
        """Ensure that the action_set is called on success."""
        self.check_output.return_value = 'Swift recon ran OK'
        actions.actions.diskusage([])
        self.check_output.assert_called_once_with(['swift-recon', '-d'])

        self.action_set.assert_called()
        self.action_fail.not_called()

    def test_check_output_failure(self):
        """Ensure that action_fail and action_set are called on
        check_output failure."""
        self.check_output.side_effect = actions.actions.CalledProcessError(
            1, "Failure")

        actions.actions.diskusage([])
        self.check_output.assert_called_once_with(['swift-recon', '-d'])

        self.action_set.assert_called()
        self.action_fail.assert_called()

    def test_failure(self):
        """Ensure that action_fail is called on any other failure."""
        self.check_output.raiseError.side_effect = Exception("Failure")
        actions.actions.diskusage([])
        self.check_output.assert_called_once_with(['swift-recon', '-d'])
        self.assertRaises("Failure")
