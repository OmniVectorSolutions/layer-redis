import os
from subprocess import check_output
from charmhelpers.core.templating import render


REDIS_SERVICE = 'snap.redis-bdx.redis-server'

REDIS_SNAP_COMMON = os.path.join('/', 'var', 'snap', 'redis-bdx', 'common')

REDIS_CONF = os.path.join(REDIS_SNAP_COMMON, 'etc', 'redis', 'redis.conf')

REDIS_DIR = os.path.join(REDIS_SNAP_COMMON, 'var', 'lib', 'redis')

REDIS_CLUSTER_CONF = os.path.join(REDIS_DIR, 'nodes.conf')

REDIS_BIN = \
    os.path.join('/', 'snap', 'redis-bdx', 'current', 'bin', 'redis-server')

REDIS_CLI = os.path.join('/', 'snap', 'bin', 'redis-bdx.redis-cli')


def redis_start_or_restart():
    if service_running(REDIS_SERVICE):
        service_restart(REDIS_SERVICE)
    else:
        service_start(REDIS_SERVICE)


def render_conf(cfg_path, cfg_tmpl, owner='root',
                group='root', ctxt={}, perms=0o644):
    if os.path.exists(cfg_path):
        os.remove(cfg_path)
    render(source=cfg_tmpl, target=cfg_path, owner=owner,
           group=group, perms=perms, context=ctxt)


def get_redis_version():
    """Return redis-server version
    """
    redis_version_out = \
        check_output([REDIS_BIN, "--version"]).decode().strip()

    for item in redis_version_out.split():
        if "v=" in item:
            return item.split("=")[1]
