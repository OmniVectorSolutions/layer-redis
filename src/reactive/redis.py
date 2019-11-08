from time import sleep
import json

from subprocess import (
    check_call,
    check_output,
    Popen,
    PIPE,
    STDOUT,
    CalledProcessError,
)

from charms.reactive import (
    clear_flag,
    endpoint_from_flag,
    hook,
    is_flag_set,
    set_flag,
    when,
    when_any,
    when_not,
)

from charmhelpers.core.hookenv import (
    application_version_set,
    local_unit,
    log,
    config,
    open_port,
    unit_private_ip,
)

from charmhelpers.core.host import (
    is_container,
    service_restart,
    service_running,
    service_start,
)

import charms.leadership

from charms.layer import status

from charms.layer.redis import (
    render_conf,
    get_redis_version,
    REDIS_CLI,
    REDIS_DIR,
    REDIS_CONF,
    REDIS_CLUSTER_CONF,
    REDIS_SERVICE,
)


def get_cluster_nodes_info():
    cluster_nodes = []
    cmd = '{} -n 0 cluster nodes'.format(REDIS_CLI)
    out = check_output(cmd, shell=True)
    for node in out.decode().split('\n')[:-1]:
        node = node.split()
        cluster_nodes.append({
            'node_ip': node[1].split("@")[0].split(":")[0],
            'node_id': node[0]
        })
    return cluster_nodes


@when_not('redis.cluster.standalone.determined')
def set_flag_for_redis_cluster_if_enabled():
    """Set utility flag for redis-cluster
    """

    if config('cluster-enabled'):
        set_flag('redis.cluster.enabled')
    set_flag('redis.cluster.standalone.determined')


@when_not('redis.system.configured')
def configure_system_for_redis():
    """Configurations for production redis.
    (only applied to non-container based machines)
    """

    if not is_container():
        with open('/etc/sysctl.d/50-redis-charm.conf', 'w') as f:
            f.write("vm.overcommit_memory = 1\n")
            f.write("net.core.somaxconn = 4096")

        # increase open file limits
        with open("/etc/security/limits.conf", "a") as f:
            f.write(
                "{}{}{}64000\n".format("*".ljust(17), 
                                       "soft".ljust(8), 
                                       "nofile".ljust(16)
                                       )
                )
            f.write(
                "{}{}{}64000\n".format("*".ljust(17), 
                                       "hard".ljust(8), 
                                       "nofile".ljust(16)
                                       )
                )
        with open("/etc/systemd/system.conf", "a") as f:
            f.write("DefaultLimitNOFILE=65536\n")

        # reload sysctl configs
        check_call(["sysctl", "-p"])

        with open("/etc/rc.local", "a") as f:
            f.write(
                "\necho never > /sys/kernel/mm/transparent_hugepage/enabled")

        with open('/sys/kernel/mm/transparent_hugepage/enabled', 'w') as f:
            f.write('never')

    set_flag('redis.system.configured')


@when('snap.installed.redis-bdx',
      'redis.system.configured',
      'redis.cluster.standalone.determined')
@when_not('redis.system.pre-init.complete')
def set_pre_init():
    """Set utility pre-init flag
    """
    set_flag('redis.system.pre-init.complete')


@when('redis.system.pre-init.complete')
@when_not('redis.ready')
def write_config_start_restart_redis():
    """Write config, restart service
    """

    ctxt = {'port': config('port'),
            'databases': config('databases'),
            'log_level': config('log-level'),
            'tcp_keepalive': config('tcp-keepalive'),
            'timeout': config('timeout'),
            'redis_dir': REDIS_DIR}

    if config('cluster-enabled'):
        ctxt['cluster_conf'] = REDIS_CLUSTER_CONF
    if config('password'):
        ctxt['password'] = config('password')

    render_conf(REDIS_CONF, 'redis.conf.tmpl', ctxt=ctxt)

    if service_running(REDIS_SERVICE):
        service_restart(REDIS_SERVICE)
    else:
        service_start(REDIS_SERVICE)

    status.active("Redis {} available".format(
        "cluster" if config('cluster-enabled') else "singleton"))
    set_flag('redis.ready')


@when('redis.ready')
@when_not('redis.port.accessible')
def open_redis_port():
    """Open redis port
    """
    open_port(config('port'))
    set_flag('redis.port.accessible')


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
        status.blocked("Cannot get redis-server version")
        return


@when('redis.cluster.enabled')
@when_not('leadership.set.init_masters')
@when_any('endpoint.cluster.peer.joined',
          'endpoint.cluster.peer.changed')
def ensure_sufficient_masters():
    """Redis enforces us to use at minimum 3 master nodes.
    Set leader flag indicating we have met the minimum # nodes.
    """

    if is_flag_set('endpoint.cluster.peer.joined'):
        endpoint = 'endpoint.cluster.peer.joined'
    elif is_flag_set('endpoint.cluster.peer.changed'):
        endpoint = 'endpoint.cluster.peer.changed'
    else:
        status.blocked('No peer endpoint set')
        return

    # Get the peers, check for min length
    peers = endpoint_from_flag(endpoint).all_units
    peer_ips = [peer._data['private-address']
                for peer in peers if peer._data is not None]
    if len(peer_ips) > 1:
        status.active(
            "Minimum # masters available, got {}.".format(len(peer_ips)+1))
        init_masters = \
            ",".join(peer_ips + [unit_private_ip()])
        charms.leadership.leader_set(init_masters=init_masters)

    clear_flag('endpoint.cluster.peer.joined')
    clear_flag('endpoint.cluster.peer.changed')


@when('redis.ready',
      'leadership.is_leader',
      'redis.cluster.enabled',
      'redis.port.accessible',
      'leadership.set.init_masters')
@when_not('redis.cluster.created',
          'leadership.set.cluster_created')
def create_redis_cluster():
    """Create redis cluster
    """

    status.maintenance("creating cluster...")

    init_masters = charms.leadership.leader_get("init_masters")
    init_masters_split = init_masters.split(",")
    init_masters_str = \
        " ".join(["{}:6379".format(ip) for ip in init_masters_split])

    cmd = ('{} --cluster create {} --cluster-replicas 0').format(
        REDIS_CLI, init_masters_str)
    p = Popen(cmd.split(), stdout=PIPE, stdin=PIPE, stderr=STDOUT)
    std_out = p.communicate(input='yes\n'.encode())[0]

    log(std_out)

    sleep(1)

    charms.leadership.leader_set(cluster_node_ips=init_masters)
    charms.leadership.leader_set(cluster_created="true")
    charms.leadership.leader_set(
        cluster_nodes_json=json.dumps(
            get_cluster_nodes_info()))

    set_flag('redis.cluster.joined')
    set_flag('redis.cluster.created')


@when('redis.ready',
      'redis.cluster.enabled')
@when_not('leadership.set.init_masters')
def waiting_for_min_masters():
    """Waiting for min masters
    """
    status.blocked("Need at least 3 master nodes to bootstrap cluster...")
    return


@when('leadership.set.cluster_node_ips')
def are_we_in_status():
    """Determine if this node is part of the cluster.
    """

    cluster_node_ips = \
        charms.leadership.leader_get("cluster_node_ips").split(",")

    if unit_private_ip() in cluster_node_ips:
        status.active("cluster successfully joined")
        set_flag('redis.cluster.joined')


@when('redis.cluster.joined')
def cluster_joined_status():
    """Set cluster joined status
    """
    status.active("successfully clustered")


@when('redis.ready',
      'redis.cluster.enabled',
      'redis.cluster.created',
      'leadership.set.cluster_created',
      'leadership.is_leader')
@when_any('endpoint.cluster.peer.joined',
          'endpoint.cluster.peer.changed')
def add_new_peer_nodes_to_cluster():
    """Add new peers to cluster
    """

    if is_flag_set('endpoint.cluster.peer.joined'):
        endpoint = 'endpoint.cluster.peer.joined'
    elif is_flag_set('endpoint.cluster.peer.changed'):
        endpoint = 'endpoint.cluster.peer.changed'
    else:
        status.blocked('No peer endpoint set')
        return

    # Get the known application peer ip addressese from juju perspective
    peers = endpoint_from_flag(endpoint).all_units
    peer_ips = [peer._data['private-address']
                for peer in peers if peer._data is not None]

    # Get the known cluster node ips from redis point of view
    cluster_node_ips = [node['node_ip'] for node in get_cluster_nodes_info()]

    # Compare the nodes in the cluster to the peer nodes that juju is aware of
    # Register nodes that are juju peers, but not part of the cluster
    node_added = False
    for ip in peer_ips:
        if ip not in cluster_node_ips:
            node_added = True
            cmd = "{} --cluster add-node {}:6379 {}:6379".format(
                 REDIS_CLI, ip, unit_private_ip())
            out = check_output(cmd, shell=True)
            log(out)

    # Give the cluster a second to recognize the new node
    sleep(1)

    if node_added:
        cluster_nodes = get_cluster_nodes_info()
        cluster_node_ips = [node['node_ip'] for node in cluster_nodes]
        cluster_node_ids = [node['node_id'] for node in cluster_nodes]

        charms.leadership.leader_set(
            cluster_node_ips=",".join(cluster_node_ips))
        charms.leadership.leader_set(
            cluster_nodes_json=json.dumps(cluster_nodes))

        # Generate the weights string for the rebalance command
        node_weights = " ".join(["{}=1".format(node_id)
                                 for node_id in cluster_node_ids])
        cmd = ("{} --cluster rebalance --cluster-weight {} "
               "--cluster-timeout 3600 --cluster-use-empty-masters "
               "{}:6379").format(REDIS_CLI, node_weights, unit_private_ip())
        out = check_output(cmd, shell=True)
        log(out)

    clear_flag('endpoint.cluster.peer.joined')
    clear_flag('endpoint.cluster.peer.changed')


# Client Relation
@when('endpoint.redis.joined')
def provide_client_endpoint_data():
    """Send data over endpoint
    """

    endpoint = endpoint_from_flag('endpoint.redis.joined')
    ctxt = {'host': unit_private_ip(), 'port': config('port')}
    if config('password'):
        ctxt['password'] = config('password')
    endpoint.configure(**ctxt)


# Set up Nagios checks when the nrpe-external-master subordinate is related
@when('nrpe-external-master.available')
@when_not('redis.nagios-setup.complete')
def setup_nagios(nagios):
    """Setup nagios check
    """

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


@hook('stop')
def rebalance_and_remove():
    """Rebalance and remove.
    Rebalance the node slots before removal.
    """
    if is_flag_set('redis.cluster.joined') and \
       not is_flag_set('redis.cluster.stopped'):
        nodes_info_json = charms.leadership.leader_get("cluster_nodes_json")
        nodes_info = json.loads(nodes_info_json)
        for node in nodes_info:
            if node['node_ip'] == unit_private_ip():
                # Rebalance slots away from node to remove
                cmd = ("{} --cluster rebalance {}:6379 "
                       "--cluster-weight {}=0").format(
                           REDIS_CLI, node['node_ip'], node['node_id'])
                out = check_output(cmd, shell=True)
                log(out)
                # TODO: Need to figure out a way to poll here.
                sleep(5)
                try:
                    # Remove node from cluster
                    cmd = "{} --cluster del-node {}:6379 {}".format(
                        REDIS_CLI, node['node_ip'], node['node_id'])
                    out = check_output(cmd, shell=True)
                    log(out)
                except CalledProcessError as e:
                    log(e)
        set_flag('redis.cluster.stopped')
