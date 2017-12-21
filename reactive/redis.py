from subprocess import call

from charms.reactive import (
    endpoint_from_flag,
    set_flag,
    register_trigger,
    when,
    when_not,
)

from charmhelpers.core.hookenv import (
    application_version_set,
    network_get,
    status_set,
    config,
    open_port
)

from charmhelpers.core.host import (
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
    with open('/etc/sysctl.conf', 'a') as f:
        f.write("\nvm.overcommit_memory = 1")
    call('sysctl vm.overcommit_memory=1'.split())

    with open('/sys/kernel/mm/transparent_hugepage/enabled', 'w') as f:
        f.write('never')

    with open('/proc/sys/net/core/somaxconn', 'w') as f:
        f.write('1024')

    set_flag('redis.system.configured')


@when_not('redis.ready')
@when('snap.installed.redis-bdx', 'redis.system.configured')
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


# Client Relation
@when('endpoint.redis.joined')
@when_not('juju.redis.available')
def provide_client_relation_data():
    status_set('maintenance', "Providing Redis endpoint")
    endpoint = endpoint_from_flag('endpoint.redis.joined')
    ctxt = {'host': PRIVATE_IP, 'port': config('port')}
    if config('password'):
        ctxt['password'] = config('password')
    endpoint.configure(**ctxt)
    status_set('active', "Endpoint: endpoint.redis.joined available")
    set_flag('juju.redis.available')
