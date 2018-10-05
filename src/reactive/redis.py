from subprocess import call

from charms.reactive import (
    clear_flag,
    endpoint_from_flag,
    hook,
    set_flag,
    when,
    when_not,
)

from charmhelpers.core.hookenv import (
    application_version_set,
    local_unit,
    network_get,
    status_set,
    config,
    open_port
)

from charmhelpers.core.host import (
    is_container,
    service_restart,
    service_running,
    service_start,
)

from charms.layer.redis import (
    render_conf,
    get_redis_version,
    REDIS_CONF,
    REDIS_SERVICE
)


PRIVATE_IP = network_get('redis')['ingress-addresses'][0]


@when_not('redis.system.configured')
def configure_system_for_redis():
    if not is_container():
        with open('/etc/sysctl.conf', 'a') as f:
            f.write("\nvm.overcommit_memory = 1")
        call('sysctl vm.overcommit_memory=1'.split())

        with open('/sys/kernel/mm/transparent_hugepage/enabled', 'w') as f:
            f.write('never')

        with open('/proc/sys/net/core/somaxconn', 'w') as f:
            f.write('1024')

    set_flag('redis.system.configured')


@when_not('redis.ready')
@when('snap.installed.redis-bdx',
      'redis.system.configured')
def config_redis():
    ctxt = {'host': PRIVATE_IP,
            'port': config('port'),
            'databases': config('databases'),
            'log_level': config('log-level'),
            'tcp_keepalive': config('tcp-keepalive'),
            'timeout': config('timeout')}

    if config('password'):
        ctxt['password'] = config('password')

    render_conf(REDIS_CONF, 'redis.conf.tmpl', ctxt=ctxt)

    if service_running(REDIS_SERVICE):
        service_restart(REDIS_SERVICE)
    else:
        service_start(REDIS_SERVICE)

    open_port(config('port'))
    status_set('active', 'Redis available')
    set_flag('redis.ready')


@when('redis.ready')
@when_not('redis.version.set')
def set_redis_version():
    """Set redis version
    """
    version = get_redis_version()
    if version:
        application_version_set(version)
        set_flag('redis.version.set')
    else:
        status_set('blocked', "Cannot get redis-server version")
        return


# Client Relation
@when('endpoint.redis.joined')
def provide_client_relation_data():
    endpoint = endpoint_from_flag('endpoint.redis.joined')
    ctxt = {'host': PRIVATE_IP, 'port': config('port')}
    if config('password'):
        ctxt['password'] = config('password')
    endpoint.configure(**ctxt)


# Set up Nagios checks when the nrpe-external-master subordinate is related
@when('nrpe-external-master.available')
@when_not('redis.nagios-setup.complete')
def setup_nagios(nagios):
    conf = config()
    unit_name = local_unit()
    check_base = '/usr/lib/nagios/plugins/'
    process_check = check_base + 'check_procs'

    web_check = [process_check, '-c', '1:1', '-a', 'bin/redis-server']
    nagios.add_check(web_check, name="redis-serverprocess",
                     description="Check for redis-server process",
                     context=conf['nagios_context'],
                     servicegroups=conf['nagios_servicegroups'],
                     unit=unit_name)

    set_flag('redis.nagios-setup.complete')


# This is triggered on any config-changed, and after an upgrade-charm - you
# don't get the latter with @when('config.changed')
@hook('config-changed')
def set_nrpe_flag():
    clear_flag('redis.nagios-setup.complete')
