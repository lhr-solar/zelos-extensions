"""Entry point of the Photon agent runtime."""

import logging
import signal
import sys
import threading

import zelos_sdk
from zelos_sdk.extensions import ConfigValidationError, load_config

from zelos_extension_photon import ACTION_PREFIX
from zelos_extension_photon.actions import STATE, register_actions

logger = logging.getLogger("photon")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)5s %(name)s: %(message)s")
    try:
        config = load_config()
    except ConfigValidationError as e:
        logger.error("Invalid configuration: %s", "; ".join(m.strip(" •") for m in e.errors))
        return 1
    STATE.configure(config)
    if not (config.get("password")):
        logger.warning("No Photon password set; actions will fail until it is added in the extension settings")

    names = register_actions(zelos_sdk.actions_registry)
    zelos_sdk.init(name=ACTION_PREFIX, actions=True, log_level="info")
    logger.info("Photon extension ready: %s, actions %s", config.get("photon_url"), ", ".join(names))

    stop = threading.Event()

    def shutdown(signum, _frame) -> None:
        logger.info("Stopping (signal %s)", signum)
        STATE.progress.cancel.set()
        stop.set()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    while not stop.wait(1.0):
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
