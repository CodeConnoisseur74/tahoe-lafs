import sys
import json
from os.path import join

from cryptography.hazmat.primitives.serialization import (
    Encoding,
    PublicFormat,
)

from twisted.internet.utils import (
    getProcessOutputAndValue,
)
from twisted.internet.defer import (
    inlineCallbacks,
    returnValue,
)

from allmydata.crypto import ed25519
from allmydata.util import base32
from allmydata.util import configutil

from . import util
from .grid import (
    create_grid,
)

import pytest_twisted


@inlineCallbacks
def _run_gm(reactor, *args, **kwargs):
    """
    Run the grid-manager process, passing all arguments as extra CLI
    args.

    :returns: all process output
    """
    output, errput, exit_code = yield getProcessOutputAndValue(
        sys.executable,
        ("-m", "allmydata.cli.grid_manager") + args,
        reactor=reactor,
        **kwargs
    )
    if exit_code != 0:
        raise util.ProcessFailed(
            RuntimeError("Exit code {}".format(exit_code)),
            output + errput,
        )
    returnValue(output)


@pytest_twisted.inlineCallbacks
def test_create_certificate(reactor, request):
    """
    The Grid Manager produces a valid, correctly-signed certificate.
    """
    gm_config = yield _run_gm(reactor, "--config", "-", "create")
    privkey_bytes = json.loads(gm_config)['private_key'].encode('ascii')
    privkey, pubkey = ed25519.signing_keypair_from_string(privkey_bytes)

    # Note that zara + her key here are arbitrary and don't match any
    # "actual" clients in the test-grid; we're just checking that the
    # Grid Manager signs this properly.
    gm_config = yield _run_gm(
        reactor, "--config", "-", "add",
        "zara", "pub-v0-kzug3ut2m7ziihf3ndpqlquuxeie4foyl36wn54myqc4wmiwe4ga",
        stdinBytes=gm_config,
    )
    zara_cert_bytes = yield _run_gm(
        reactor,  "--config", "-", "sign", "zara", "1",
        stdinBytes=gm_config,
    )
    zara_cert = json.loads(zara_cert_bytes)

    # confirm that zara's certificate is made by the Grid Manager
    # (.verify returns None on success, raises exception on error)
    pubkey.verify(
        base32.a2b(zara_cert['signature'].encode('ascii')),
        zara_cert['certificate'].encode('ascii'),
    )


@pytest_twisted.inlineCallbacks
def test_remove_client(reactor, request):
    """
    A Grid Manager can add and successfully remove a client
    """
    gm_config = yield _run_gm(
        reactor, "--config", "-", "create",
    )

    gm_config = yield _run_gm(
        reactor, "--config", "-", "add",
        "zara", "pub-v0-kzug3ut2m7ziihf3ndpqlquuxeie4foyl36wn54myqc4wmiwe4ga",
        stdinBytes=gm_config,
    )
    gm_config = yield _run_gm(
        reactor, "--config", "-", "add",
        "yakov", "pub-v0-kvxhb3nexybmipkrar2ztfrwp4uxxsmrjzkpzafit3ket4u5yldq",
        stdinBytes=gm_config,
    )
    assert "zara" in json.loads(gm_config)['storage_servers']
    assert "yakov" in json.loads(gm_config)['storage_servers']

    gm_config = yield _run_gm(
        reactor, "--config", "-", "remove",
        "zara",
        stdinBytes=gm_config,
    )
    assert "zara" not in json.loads(gm_config)['storage_servers']
    assert "yakov" in json.loads(gm_config)['storage_servers']


@pytest_twisted.inlineCallbacks
def test_remove_last_client(reactor, request):
    """
    A Grid Manager can remove all clients
    """
    gm_config = yield _run_gm(
        reactor, "--config", "-", "create",
    )

    gm_config = yield _run_gm(
        reactor, "--config", "-", "add",
        "zara", "pub-v0-kzug3ut2m7ziihf3ndpqlquuxeie4foyl36wn54myqc4wmiwe4ga",
        stdinBytes=gm_config,
    )
    assert "zara" in json.loads(gm_config)['storage_servers']

    gm_config = yield _run_gm(
        reactor, "--config", "-", "remove",
        "zara",
        stdinBytes=gm_config,
    )
    # there are no storage servers left at all now
    assert "storage_servers" not in json.loads(gm_config)


@pytest_twisted.inlineCallbacks
def test_add_remove_client_file(reactor, request, temp_dir):
    """
    A Grid Manager can add and successfully remove a client (when
    keeping data on disk)
    """
    gmconfig = join(temp_dir, "gmtest")
    gmconfig_file = join(temp_dir, "gmtest", "config.json")
    yield _run_gm(
        reactor, "--config", gmconfig, "create",
    )

    yield _run_gm(
        reactor, "--config", gmconfig, "add",
        "zara", "pub-v0-kzug3ut2m7ziihf3ndpqlquuxeie4foyl36wn54myqc4wmiwe4ga",
    )
    yield _run_gm(
        reactor, "--config", gmconfig, "add",
        "yakov", "pub-v0-kvxhb3nexybmipkrar2ztfrwp4uxxsmrjzkpzafit3ket4u5yldq",
    )
    assert "zara" in json.load(open(gmconfig_file, "r"))['storage_servers']
    assert "yakov" in json.load(open(gmconfig_file, "r"))['storage_servers']

    yield _run_gm(
        reactor, "--config", gmconfig, "remove",
        "zara",
    )
    assert "zara" not in json.load(open(gmconfig_file, "r"))['storage_servers']
    assert "yakov" in json.load(open(gmconfig_file, "r"))['storage_servers']


@pytest_twisted.inlineCallbacks
def test_reject_storage_server(reactor, request, temp_dir, flog_gatherer, port_allocator):
    """
    A client with happines=2 fails to upload to a Grid when it is
    using Grid Manager and there is only 1 storage server with a valid
    certificate.
    """
    grid = yield create_grid(reactor, request, temp_dir, flog_gatherer, port_allocator)
    storage0 = yield grid.add_storage_node()
    _ = yield grid.add_storage_node()

    gm_config = yield _run_gm(
        reactor, "--config", "-", "create",
    )
    gm_privkey_bytes = json.loads(gm_config)['private_key'].encode('ascii')
    gm_privkey, gm_pubkey = ed25519.signing_keypair_from_string(gm_privkey_bytes)

    # create certificate for the first storage-server
    pubkey_fname = join(storage0.process.node_dir, "node.pubkey")
    with open(pubkey_fname, 'r') as f:
        pubkey_str = f.read().strip()

    gm_config = yield _run_gm(
        reactor, "--config", "-", "add",
        "storage0", pubkey_str,
        stdinBytes=gm_config,
    )
    assert json.loads(gm_config)['storage_servers'].keys() == {'storage0'}

    print("inserting certificate")
    cert = yield _run_gm(
        reactor, "--config", "-", "sign", "storage0", "1",
        stdinBytes=gm_config,
    )
    print(cert)

    yield util.run_tahoe(
        reactor, request, "--node-directory", storage0.process.node_dir,
        "admin", "add-grid-manager-cert",
        "--name", "default",
        "--filename", "-",
        stdin=cert,
    )

    # re-start this storage server
    yield storage0.restart(reactor, request)

    import time
    time.sleep(1)

    # now only one storage-server has the certificate .. configure
    # diana to have the grid-manager certificate

    diana = yield grid.add_client("diana", needed=2, happy=2, total=2)

    config = configutil.get_config(join(diana.process.node_dir, "tahoe.cfg"))
    config.add_section("grid_managers")
    config.set("grid_managers", "test", str(ed25519.string_from_verifying_key(gm_pubkey), "ascii"))
    with open(join(diana.process.node_dir, "tahoe.cfg"), "w") as f:
        config.write(f)

    yield diana.restart(reactor, request, servers=2)

    # try to put something into the grid, which should fail (because
    # diana has happy=2 but should only find storage0 to be acceptable
    # to upload to)

    # Takes a little bit of time for node to connect:
    for i in range(10):
        try:
            yield util.run_tahoe(
                reactor, request, "--node-directory", diana.process.node_dir,
                "put", "-",
                stdin=b"some content\n" * 200,
            )
            assert False, "Should get a failure"
        except util.ProcessFailed as e:
            if b'UploadUnhappinessError' in e.output:
                # We're done! We've succeeded.
                return
        time.sleep(0.2)

    assert False, "Failed to see one of out of two servers"


@pytest_twisted.inlineCallbacks
def test_identity(reactor, request, temp_dir):
    """
    Dump public key to CLI
    """
    gm_config = join(temp_dir, "test_identity")
    yield _run_gm(
        reactor, "--config", gm_config, "create",
    )

    # ask the CLI for the grid-manager pubkey
    pubkey = yield _run_gm(
        reactor, "--config", gm_config, "public-identity",
    )
    alleged_pubkey = ed25519.verifying_key_from_string(pubkey.strip())

    # load the grid-manager pubkey "ourselves"
    with open(join(gm_config, "config.json"), "r") as f:
        real_config = json.load(f)
    real_privkey, real_pubkey = ed25519.signing_keypair_from_string(
        real_config["private_key"].encode("ascii"),
    )

    # confirm the CLI told us the correct thing
    alleged_bytes = alleged_pubkey.public_bytes(Encoding.Raw, PublicFormat.Raw)
    real_bytes = real_pubkey.public_bytes(Encoding.Raw, PublicFormat.Raw)
    assert alleged_bytes == real_bytes, "Keys don't match"
