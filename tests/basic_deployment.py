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

import amulet
import swiftclient
import time

from charmhelpers.contrib.openstack.amulet.deployment import (
    OpenStackAmuletDeployment
)

from charmhelpers.contrib.openstack.amulet.utils import (
    OpenStackAmuletUtils,
    DEBUG
)
from charmhelpers.contrib.openstack.utils import CompareOpenStackReleases

# Use DEBUG to turn on debug logging
u = OpenStackAmuletUtils(DEBUG)


class SwiftProxyBasicDeployment(OpenStackAmuletDeployment):
    """Amulet tests on a basic swift-proxy deployment."""

    def __init__(self, series, openstack=None, source=None, stable=False):
        """Deploy the entire test environment."""
        super(SwiftProxyBasicDeployment, self).__init__(series, openstack,
                                                        source, stable)
        self._add_services()
        self._add_relations()
        self._configure_services()
        self._deploy()

        u.log.info('Waiting on extended status checks...')
        exclude_services = []
        self._auto_wait_for_status(exclude_services=exclude_services)

        self.d.sentry.wait()
        self._initialize_tests()

    def _add_services(self):
        """Add services

           Add the services that we're testing, where swift-proxy is local,
           and the rest of the service are from lp branches that are
           compatible with the local charm (e.g. stable or next).
           """
        this_service = {'name': 'swift-proxy'}
        other_services = [
            {'name': 'percona-cluster', 'constraints': {'mem': '3072M'}},
            {'name': 'keystone'},
            {'name': 'glance'},
            {'name': 'swift-storage'}
        ]
        super(SwiftProxyBasicDeployment, self)._add_services(this_service,
                                                             other_services)

    def _add_relations(self):
        """Add all of the relations for the services."""
        relations = {
            'keystone:shared-db': 'percona-cluster:shared-db',
            'swift-proxy:identity-service': 'keystone:identity-service',
            'swift-storage:swift-storage': 'swift-proxy:swift-storage',
            'glance:identity-service': 'keystone:identity-service',
            'glance:shared-db': 'percona-cluster:shared-db',
            'glance:object-store': 'swift-proxy:object-store'
        }
        super(SwiftProxyBasicDeployment, self)._add_relations(relations)

    def _configure_services(self):
        """Configure all of the services."""
        keystone_config = {
            'admin-password': 'openstack',
            'admin-token': 'ubuntutesting'
        }
        swift_proxy_config = {
            'zone-assignment': 'manual',
            'replicas': '1',
            'swift-hash': 'fdfef9d4-8b06-11e2-8ac0-531c923c8fae'
        }
        swift_storage_config = {
            'zone': '1',
            'block-device': 'vdb',
            'overwrite': 'true'
        }
        pxc_config = {
            'dataset-size': '25%',
            'max-connections': 1000,
            'root-password': 'ChangeMe123',
            'sst-password': 'ChangeMe123',
        }
        configs = {
            'keystone': keystone_config,
            'swift-proxy': swift_proxy_config,
            'swift-storage': swift_storage_config,
            'percona-cluster': pxc_config,
        }
        super(SwiftProxyBasicDeployment, self)._configure_services(configs)

    def _initialize_tests(self):
        """Perform final initialization before tests get run."""
        # Access the sentries for inspecting service units
        self.pxc_sentry = self.d.sentry['percona-cluster'][0]
        self.keystone_sentry = self.d.sentry['keystone'][0]
        self.glance_sentry = self.d.sentry['glance'][0]
        self.swift_proxy_sentry = self.d.sentry['swift-proxy'][0]
        self.swift_storage_sentry = self.d.sentry['swift-storage'][0]

        u.log.debug('openstack release val: {}'.format(
            self._get_openstack_release()))
        u.log.debug('openstack release str: {}'.format(
            self._get_openstack_release_string()))

        # Authenticate admin with keystone
        self.keystone = u.authenticate_keystone_admin(self.keystone_sentry,
                                                      user='admin',
                                                      password='openstack',
                                                      tenant='admin')

        # Authenticate admin with glance endpoint
        self.glance = u.authenticate_glance_admin(self.keystone)

        # Authenticate swift user
        keystone_relation = self.keystone_sentry.relation(
            'identity-service', 'swift-proxy:identity-service')
        ep = self.keystone.service_catalog.url_for(service_type='identity',
                                                   interface='publicURL')
        self.swift = swiftclient.Connection(
            authurl=ep,
            user=keystone_relation['service_username'],
            key=keystone_relation['service_password'],
            tenant_name=keystone_relation['service_tenant'],
            auth_version='2.0')

        # Create a demo tenant/role/user
        self.demo_tenant = 'demoTenant'
        self.demo_role = 'demoRole'
        self.demo_user = 'demoUser'
        if not u.tenant_exists(self.keystone, self.demo_tenant):
            tenant = self.keystone.tenants.create(tenant_name=self.demo_tenant,
                                                  description='demo tenant',
                                                  enabled=True)
            self.keystone.roles.create(name=self.demo_role)
            self.keystone.users.create(name=self.demo_user,
                                       password='password',
                                       tenant_id=tenant.id,
                                       email='demo@demo.com')

        # Authenticate demo user with keystone
        self.keystone_demo = \
            u.authenticate_keystone_user(self.keystone, user=self.demo_user,
                                         password='password',
                                         tenant=self.demo_tenant)

    def test_100_services(self):
        """Verify the expected services are running on the corresponding
           service units."""
        u.log.debug('Checking system services...')
        swift_storage_services = ['swift-account',
                                  'swift-account-auditor',
                                  'swift-account-reaper',
                                  'swift-account-replicator',
                                  'swift-container',
                                  'swift-container-auditor',
                                  'swift-container-replicator',
                                  'swift-container-updater',
                                  'swift-object',
                                  'swift-object-auditor',
                                  'swift-object-replicator',
                                  'swift-object-updater',
                                  'swift-container-sync']
        service_names = {
            self.keystone_sentry: ['keystone'],
            self.glance_sentry: ['glance-registry',
                                 'glance-api'],
            self.swift_proxy_sentry: ['swift-proxy'],
            self.swift_storage_sentry: swift_storage_services
        }

        if self._get_openstack_release() >= self.trusty_liberty:
            service_names[self.keystone_sentry] = ['apache2']

        ret = u.validate_services_by_name(service_names)
        if ret:
            amulet.raise_status(amulet.FAIL, msg=ret)

    def test_102_users(self):
        """Verify all existing roles."""
        u.log.debug('Checking keystone users...')
        user1 = {'name': 'demoUser',
                 'enabled': True,
                 'tenantId': u.not_null,
                 'id': u.not_null,
                 'email': 'demo@demo.com'}
        user2 = {'name': 'admin',
                 'enabled': True,
                 'tenantId': u.not_null,
                 'id': u.not_null,
                 'email': 'juju@localhost'}
        user3 = {'name': 'glance',
                 'enabled': True,
                 'tenantId': u.not_null,
                 'id': u.not_null,
                 'email': u'juju@localhost'}
        user4 = {'name': 's3_swift',
                 'enabled': True,
                 'tenantId': u.not_null,
                 'id': u.not_null,
                 'email': u'juju@localhost'}
        expected = [user1, user2, user3, user4]
        actual = self.keystone.users.list()

        ret = u.validate_user_data(expected, actual)
        if ret:
            amulet.raise_status(amulet.FAIL, msg=ret)

    def test_104_keystone_service_catalog(self):
        """Verify that the service catalog endpoint data is valid."""
        u.log.debug('Checking keystone service catalog...')
        endpoint_id = {'adminURL': u.valid_url,
                       'region': 'RegionOne',
                       'publicURL': u.valid_url,
                       'internalURL': u.valid_url,
                       'id': u.not_null}

        expected = {'image': [endpoint_id], 'object-store': [endpoint_id],
                    'identity': [endpoint_id], 's3': [endpoint_id]}
        actual = self.keystone_demo.service_catalog.get_endpoints()

        ret = u.validate_svc_catalog_endpoint_data(expected, actual)
        if ret:
            amulet.raise_status(amulet.FAIL, msg=ret)

    def test_106_swift_object_store_endpoint(self):
        """Verify the swift object-store endpoint data."""
        u.log.debug('Checking keystone endpoint for swift object store...')
        endpoints = self.keystone.endpoints.list()
        admin_port = internal_port = public_port = '8080'
        expected = {'id': u.not_null,
                    'region': 'RegionOne',
                    'adminurl': u.valid_url,
                    'internalurl': u.valid_url,
                    'publicurl': u.valid_url,
                    'service_id': u.not_null}

        ret = u.validate_endpoint_data(endpoints, admin_port, internal_port,
                                       public_port, expected)
        if ret:
            message = 'object-store endpoint: {}'.format(ret)
            amulet.raise_status(amulet.FAIL, msg=message)

    def test_200_swift_proxy_identity_service_relation(self):
        """Verify the swift-proxy to keystone identity relation data."""
        u.log.debug('Checking swift-proxy:keystone identity relation...')
        unit = self.swift_proxy_sentry
        relation = ['identity-service', 'keystone:identity-service']
        expected = {
            'swift_service': 'swift',
            'swift_region': 'RegionOne',
            'swift_public_url': u.valid_url,
            'swift_internal_url': u.valid_url,
            'swift_admin_url': u.valid_url,
            's3_service': 's3',
            's3_region': 'RegionOne',
            's3_public_url': u.valid_url,
            's3_internal_url': u.valid_url,
            's3_admin_url': u.valid_url,
            'private-address': u.valid_ip,
        }

        ret = u.validate_relation_data(unit, relation, expected)
        if ret:
            message = u.relation_error('swift-proxy identity-service', ret)
            amulet.raise_status(amulet.FAIL, msg=message)

    def test_202_keystone_identity_service_relation(self):
        """Verify the keystone to swift-proxy identity relation data."""
        u.log.debug('Checking keystone:swift-proxy identity relation...')
        unit = self.keystone_sentry
        relation = ['identity-service', 'swift-proxy:identity-service']
        expected = {
            'service_protocol': 'http',
            'service_tenant': 'services',
            'admin_token': 'ubuntutesting',
            'service_password': u.not_null,
            'service_port': '5000',
            'auth_port': '35357',
            'auth_protocol': 'http',
            'private-address': u.valid_ip,
            'auth_host': u.valid_ip,
            'service_username': 's3_swift',
            'service_tenant_id': u.not_null,
            'service_host': u.valid_ip
        }

        ret = u.validate_relation_data(unit, relation, expected)
        if ret:
            message = u.relation_error('keystone identity-service', ret)
            amulet.raise_status(amulet.FAIL, msg=message)

    def test_204_swift_storage_swift_storage_relation(self):
        """Verify the swift-storage to swift-proxy swift-storage relation
           data."""
        u.log.debug('Checking swift:swift-proxy swift-storage relation...')
        unit = self.swift_storage_sentry
        relation = ['swift-storage', 'swift-proxy:swift-storage']
        expected = {
            'account_port': '6002',
            'zone': '1',
            'object_port': '6000',
            'container_port': '6001',
            'private-address': u.valid_ip,
            'device': 'vdb'
        }

        ret = u.validate_relation_data(unit, relation, expected)
        if ret:
            message = u.relation_error('swift-storage swift-storage', ret)
            amulet.raise_status(amulet.FAIL, msg=message)

    def test_206_swift_proxy_swift_storage_relation(self):
        """Verify the swift-proxy to swift-storage swift-storage relation
           data."""
        u.log.debug('Checking swift-proxy:swift swift-storage relation...')
        unit = self.swift_proxy_sentry
        relation = ['swift-storage', 'swift-storage:swift-storage']
        expected = {
            'private-address': u.valid_ip,
            'trigger': u.not_null,
            'rings_url': u.valid_url,
            'swift_hash': u.not_null
        }

        ret = u.validate_relation_data(unit, relation, expected)
        if ret:
            message = u.relation_error('swift-proxy swift-storage', ret)
            amulet.raise_status(amulet.FAIL, msg=message)

    def test_208_glance_object_store_relation(self):
        """Verify the glance to swift-proxy object-store relation data."""
        u.log.debug('Checking glance:swift-proxy object-store relation...')
        unit = self.glance_sentry
        relation = ['object-store', 'swift-proxy:object-store']
        expected = {'private-address': u.valid_ip}

        ret = u.validate_relation_data(unit, relation, expected)
        if ret:
            message = u.relation_error('glance object-store', ret)
            amulet.raise_status(amulet.FAIL, msg=message)

    def test_210_swift_proxy_object_store_relation(self):
        """Verify the swift-proxy to glance object-store relation data."""
        u.log.debug('Checking swift-proxy:glance object-store relation...')
        unit = self.swift_proxy_sentry
        relation = ['object-store', 'glance:object-store']
        expected = {'private-address': u.valid_ip}
        ret = u.validate_relation_data(unit, relation, expected)
        if ret:
            message = u.relation_error('swift-proxy object-store', ret)
            amulet.raise_status(amulet.FAIL, msg=message)

    def test_300_swift_config(self):
        """Verify the data in the swift-hash section of the swift config
           file."""
        u.log.debug('Checking swift config...')
        unit = self.swift_storage_sentry
        conf = '/etc/swift/swift.conf'
        swift_proxy_relation = self.swift_proxy_sentry.relation(
            'swift-storage', 'swift-storage:swift-storage')
        expected = {
            'swift_hash_path_suffix': swift_proxy_relation['swift_hash']
        }

        ret = u.validate_config_data(unit, conf, 'swift-hash', expected)
        if ret:
            message = "swift config error: {}".format(ret)
            amulet.raise_status(amulet.FAIL, msg=message)

    def test_302_proxy_server_config(self, auth_api_version='2.0'):
        """Verify the data in the proxy-server config file."""
        u.log.debug("Checking swift proxy-server config auth_api_version={}..."
                    "".format(auth_api_version))
        unit = self.swift_proxy_sentry
        conf = '/etc/swift/proxy-server.conf'
        keystone_relation = self.keystone_sentry.relation(
            'identity-service', 'swift-proxy:identity-service')
        swift_proxy_relation = unit.relation(
            'identity-service', 'keystone:identity-service')
        swift_proxy_ip = swift_proxy_relation['private-address']
        auth_host = keystone_relation['auth_host']
        auth_protocol = keystone_relation['auth_protocol']

        expected = {
            'DEFAULT': {
                'bind_port': '8070',
                'user': 'swift',
                'log_name': 'swift',
                'log_facility': 'LOG_LOCAL0',
                'log_level': 'INFO',
                'log_headers': 'False',
                'log_address': '/dev/log'
            },
            'pipeline:main': {
                'pipeline': 'gatekeeper healthcheck proxy-logging cache '
                            'swift3 s3token container_sync bulk tempurl '
                            'slo dlo formpost authtoken keystoneauth '
                            'staticweb container-quotas account-quotas '
                            'proxy-logging proxy-server'
            },
            'app:proxy-server': {
                'use': 'egg:swift#proxy',
                'allow_account_management': 'true',
                'account_autocreate': 'true',
                'node_timeout': '60',
                'recoverable_node_timeout': '30'
            },
            'filter:tempauth': {
                'use': 'egg:swift#tempauth',
                'user_system_root': 'testpass .admin https://{}:8080/v1/'
                                    'AUTH_system'.format(swift_proxy_ip)
            },
            'filter:healthcheck': {'use': 'egg:swift#healthcheck'},
            'filter:cache': {
                'use': 'egg:swift#memcache',
                'memcache_servers': '{}:11211'.format(swift_proxy_ip)
            },
            'filter:account-quotas': {'use': 'egg:swift#account_quotas'},
            'filter:container-quotas': {'use': 'egg:swift#container_quotas'},
            'filter:proxy-logging': {'use': 'egg:swift#proxy_logging'},
            'filter:staticweb': {'use': 'egg:swift#staticweb'},
            'filter:bulk': {'use': 'egg:swift#bulk'},
            'filter:slo': {'use': 'egg:swift#slo'},
            'filter:dlo': {'use': 'egg:swift#dlo'},
            'filter:formpost': {'use': 'egg:swift#formpost'},
            'filter:tempurl': {'use': 'egg:swift#tempurl'},
            'filter:container_sync': {'use': 'egg:swift#container_sync'},
            'filter:gatekeeper': {'use': 'egg:swift#gatekeeper'},
            'filter:keystoneauth': {
                'use': 'egg:swift#keystoneauth',
                'operator_roles': 'Member,Admin'
            },
            'filter:authtoken': {
                'auth_uri': '{}://{}:{}'.format(
                    auth_protocol,
                    auth_host,
                    keystone_relation['service_port']),
                'delay_auth_decision': 'true',
                'signing_dir': '/var/cache/swift',
                'cache': 'swift.cache'
            },
            'filter:swift3': {'use': 'egg:swift3#swift3'}
        }
        if auth_api_version == '2.0':
            expected['filter:authtoken'].update({
                'admin_tenant_name': keystone_relation['service_tenant'],
                'admin_user': keystone_relation['service_username'],
                'admin_password': keystone_relation['service_password'],
            })

        if self._get_openstack_release() >= self.trusty_mitaka:
            expected['pipeline:main'] = {
                'pipeline': 'catch_errors gatekeeper healthcheck proxy-logging'
                ' cache swift3 s3token container_sync bulk tempurl slo dlo'
                ' formpost authtoken keystoneauth staticweb'
                ' versioned_writes container-quotas account-quotas'
                ' proxy-logging proxy-server'
            }

        if self._get_openstack_release() >= self.trusty_kilo:
            # Kilo and later
            expected['filter:authtoken'].update({
                'paste.filter_factory': 'keystonemiddleware.auth_token:'
                                        'filter_factory',
            })
            if auth_api_version == '3':
                expected['filter:authtoken'].update({
                    'auth_url': '{}://{}:{}'.format(
                        auth_protocol,
                        auth_host,
                        keystone_relation['auth_port']),
                    'auth_plugin': 'password',
                    'username': keystone_relation['service_username'],
                    'password': keystone_relation['service_password'],
                    'project_domain_name': keystone_relation['service_domain'],
                    'user_domain_name': keystone_relation['service_domain'],
                    'project_name': keystone_relation['service_tenant'],
                })
            else:
                expected['filter:authtoken'].update({
                    'identity_uri': '{}://{}:{}'.format(
                        auth_protocol,
                        auth_host,
                        keystone_relation['auth_port']),
                })
            expected['filter:s3token'] = {
                # No section commonality with J and earlier
                'paste.filter_factory': 'keystonemiddleware.s3_token'
                                        ':filter_factory',
                'auth_uri': '{}://{}:{}'.format(
                    auth_protocol,
                    auth_host,
                    keystone_relation['auth_port']),
            }
        else:
            # Juno and earlier
            expected['filter:authtoken'].update({
                'paste.filter_factory': 'keystoneclient.middleware.'
                                        'auth_token:filter_factory',
                'auth_host': auth_host,
                'auth_port': keystone_relation['auth_port'],
                'auth_protocol': auth_protocol,
            })
            expected['filter:s3token'] = {
                # No section commonality with K and later
                'paste.filter_factory': 'keystoneclient.middleware.'
                                        's3_token:filter_factory',
                'auth_port': keystone_relation['auth_port'],
                'auth_host': keystone_relation['auth_host'],
                'service_host': keystone_relation['service_host'],
                'service_port': keystone_relation['service_port'],
                'auth_protocol': keystone_relation['auth_protocol'],
                'auth_token': keystone_relation['admin_token'],
                'admin_token': keystone_relation['admin_token']
            }

        for section, pairs in expected.iteritems():
            ret = u.validate_config_data(unit, conf, section, pairs)
            if ret:
                message = "proxy-server config error: {}".format(ret)
                amulet.raise_status(amulet.FAIL, msg=message)

    def test_400_swift_backed_image_create(self):
        """Create an instance in glance, which is backed by swift, and validate
        that some of the metadata for the image match in glance and swift."""
        u.log.debug('Checking swift objects and containers with a '
                    'swift-backed glance image...')

        # Create swift-backed glance image
        img_new = u.create_cirros_image(self.glance, "cirros-image-1")
        img_id = img_new.id
        img_md5 = img_new.checksum
        img_size = img_new.size

        # Validate that swift object's checksum/size match that from glance
        headers, containers = self.swift.get_account()
        if len(containers) != 1:
            msg = "Expected 1 swift container, found {}".format(
                len(containers))
            amulet.raise_status(amulet.FAIL, msg=msg)

        container_name = containers[0].get('name')

        headers, objects = self.swift.get_container(container_name)
        if len(objects) != 1:
            msg = "Expected 1 swift object, found {}".format(len(objects))
            amulet.raise_status(amulet.FAIL, msg=msg)

        swift_object_size = objects[0].get('bytes')
        swift_object_md5 = objects[0].get('hash')

        if img_size != swift_object_size:
            msg = "Glance image size {} != swift object size {}".format(
                img_size, swift_object_size)
            amulet.raise_status(amulet.FAIL, msg=msg)

        if img_md5 != swift_object_md5:
            msg = "Glance image hash {} != swift object hash {}".format(
                img_md5, swift_object_md5)
            amulet.raise_status(amulet.FAIL, msg=msg)

        # Cleanup
        u.delete_resource(self.glance.images, img_id, msg="glance image")
        u.log.info('OK')

    def _set_auth_api_version(self, api_version, retry_count=5):
        """Change Keystone preferred-api-version, wait for: propagation to
           relation data, update of service configuration file and restart of
           services on swift-proxy unit."""
        configs = {'keystone': {'preferred-api-version': api_version}}
        super(SwiftProxyBasicDeployment, self)._configure_services(configs)
        mtime = u.get_sentry_time(self.swift_proxy_sentry)
        for i in range(retry_count, -1, -1):
            ks_gl_rel = self.keystone_sentry.relation(
                'identity-service', 'glance:identity-service')
            ks_sw_rel = self.keystone_sentry.relation(
                'identity-service', 'swift-proxy:identity-service')
            if not (ks_gl_rel['api_version'] == api_version and
                    ks_sw_rel['api_version'] == api_version):
                u.log.info("change of api_version not propagated yet "
                           "retries left: '%d' "
                           "glance:identity-service api_version: '%s' "
                           "swift-proxy:identity-service api_version: '%s' "
                           % (i,
                              ks_gl_rel['api_version'],
                              ks_sw_rel['api_version']))
                u.log.info("sleeping %d seconds..." % i)
                time.sleep(i)
            elif not u.validate_service_config_changed(
                    self.swift_proxy_sentry,
                    mtime,
                    'swift-proxy-server',
                    '/etc/swift/proxy-server.conf',
                    sleep_time=i):
                msg = "swift-proxy-server didn't restart after change of "\
                      "api_version"
                amulet.raise_status(amulet.FAIL, msg=msg)
            else:
                return True
        return False

    def test_keystone_v3(self):
        """Verify that the service is configured and operates correctly when
           using Keystone v3 auth."""
        os_release = self._get_openstack_release_string()
        if CompareOpenStackReleases(os_release) < 'kilo':
            u.log.info('Skipping test, {} < kilo'.format(os_release))
            return
        u.log.info('Checking that service is configured and operate correctly '
                   'when using Keystine v3 auth...')
        if not self._set_auth_api_version('3'):
            msg = "Unable to set auth_api_version to '3'"
            amulet.raise_status(amulet.FAIL, msg=msg)
            return
        self.test_302_proxy_server_config(auth_api_version='3')
        self.test_400_swift_backed_image_create()

    def test_900_restart_on_config_change(self):
        """Verify that the specified services are restarted when the config
           is changed."""
        u.log.info('Checking that conf files and system services respond '
                   'to a charm config change...')

        sentry = self.swift_proxy_sentry
        juju_service = 'swift-proxy'

        # Process names, corresponding conf files
        services = {'swift-proxy-server': '/etc/swift/proxy-server.conf'}

        # Expected default and alternate values
        set_default = {'node-timeout': '60'}
        set_alternate = {'node-timeout': '90'}

        # Make config change, check for service restarts
        u.log.debug('Making config change on {}...'.format(juju_service))
        mtime = u.get_sentry_time(sentry)
        self.d.configure(juju_service, set_alternate)

        sleep_time = 40
        for s, conf_file in services.iteritems():
            u.log.debug("Checking that service restarted: {}".format(s))
            if not u.validate_service_config_changed(sentry, mtime, s,
                                                     conf_file,
                                                     sleep_time=sleep_time):
                self.d.configure(juju_service, set_default)
                msg = "service {} didn't restart after config change".format(s)
                amulet.raise_status(amulet.FAIL, msg=msg)
            sleep_time = 0

        self.d.configure(juju_service, set_default)

    def test_901_no_restart_on_config_change_when_paused(self):
        """Verify that the specified services are not restarted when the config
           is changed and the unit is paused."""
        u.log.info('Checking that system services do not get restarted  '
                   'when charm config changes but unit is paused...')
        sentry = self.swift_proxy_sentry
        juju_service = 'swift-proxy'

        # Expected default and alternate values
        set_default = {'node-timeout': '60'}
        set_alternate = {'node-timeout': '90'}

        services = ['swift-proxy', 'haproxy', 'apache2', 'memcached']

        # Pause the unit
        u.log.debug('Pausing the unit...')
        pause_action_id = u.run_action(sentry, "pause")
        assert u.wait_on_action(pause_action_id), "Pause action failed."
        # Make config change, check for service restarts
        u.log.debug('Making config change on {}...'.format(juju_service))
        self.d.configure(juju_service, set_alternate)

        for service in services:
            u.log.debug("Checking that service didn't start while "
                        "paused: {}".format(service))
            # No explicit assert because get_process_id_list will do it for us
            u.get_process_id_list(
                sentry, service, expect_success=False)

        self.d.configure(juju_service, set_default)
        resume_action_id = u.run_action(sentry, "resume")
        assert u.wait_on_action(resume_action_id), "Resume action failed."

    def _assert_services(self, should_run):
        swift_proxy_services = ['swift-proxy-server',
                                'haproxy',
                                'apache2',
                                'memcached']
        u.get_unit_process_ids(
            {self.swift_proxy_sentry: swift_proxy_services},
            expect_success=should_run)
        # No point using validate_unit_process_ids, since we don't
        # care about how many PIDs, merely that they're running, so
        # would populate expected with either True or False. This
        # validation is already performed in get_process_id_list

    def _test_pause(self):
        u.log.info("Testing pause action")
        self._assert_services(should_run=True)
        pause_action_id = u.run_action(self.swift_proxy_sentry, "pause")
        assert u.wait_on_action(pause_action_id), "Pause action failed."

        self._assert_services(should_run=False)
        status, message = u.status_get(self.swift_proxy_sentry)
        if status != "maintenance":
            msg = ("Pause action failed to move unit to maintenance "
                   "status (got {} instead)".format(status))
            amulet.raise_status(amulet.FAIL, msg=msg)
        if message != "Paused. Use 'resume' action to resume normal service.":
            msg = ("Pause action failed to set message"
                   " (got {} instead)".format(message))
            amulet.raise_status(amulet.FAIL, msg=msg)

    def _test_resume(self):
        u.log.info("Testing resume action")
        # service is left paused by _test_pause
        self._assert_services(should_run=False)
        resume_action_id = u.run_action(self.swift_proxy_sentry, "resume")
        assert u.wait_on_action(resume_action_id), "Resume action failed."

        self._assert_services(should_run=True)
        status, message = u.status_get(self.swift_proxy_sentry)
        if status != "active":
            msg = ("Resume action failed to move unit to active "
                   "status (got {} instead)".format(status))
            amulet.raise_status(amulet.FAIL, msg=msg)
        if message != "Unit is ready":
            msg = ("Resume action failed to clear message"
                   " (got {} instead)".format(message))
            amulet.raise_status(amulet.FAIL, msg=msg)

    def test_902_pause_resume_actions(self):
        """Pause and then resume swift-proxy."""
        u.log.debug('Checking pause/resume actions...')
        self._test_pause()
        self._test_resume()

    def test_903_disk_usage_action(self):
        """diskusage action can be run"""
        u.log.info("Testing diskusage action")
        action_id = u.run_action(self.swift_proxy_sentry, "diskusage")
        assert u.wait_on_action(action_id), "diskusage action failed."

        u.log.info('OK')
