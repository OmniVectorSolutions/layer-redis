import os
from subprocess import check_output
from charmhelpers.core.templating import render


REDIS_SERVICE = 'snap.redis-bdx.redis-server'

REDIS_CONF = \
    os.path.join('/', 'var', 'snap', 'redis-bdx',
                 'common', 'etc', 'redis', 'redis.conf')

REDIS_BIN = \
    os.path.join('/', 'snap', 'redis-bdx', 'current', 'bin', 'redis-server')


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
