import collections.abc
from importlib.metadata import version

from unitelabs.cdk import Connector
from unitelabs.cdk.features.core.lock_controller import LockController

from .config import Ur5eConnectorConfig
from .features.robot_controller import RobotController
from .features.simulation_controller import SimulationController
from .io import Ur5eProtocol

__version__ = version("unitelabs-ur5e-connector")


async def create_app(config: Ur5eConnectorConfig) -> collections.abc.AsyncGenerator[Connector, None]:
    """Create the connector and manage its connection lifecycle."""
    app = Connector(config)
    protocol = Ur5eProtocol(
        simulation_mode=config.simulation_mode,
        host=config.robot_host,
        port=config.dashboard_port,
        enable_program_control=config.enable_program_control,
        approved_program=config.approved_program,
        enable_base_step=config.enable_base_step,
        base_step_receipt=config.base_step_receipt,
        base_step_reference=config.base_step_reference,
        enable_elbow_step=config.enable_elbow_step,
        elbow_step_receipt=config.elbow_step_receipt,
        enable_joint_testing=config.enable_joint_testing,
        enable_continuous_motion=config.enable_continuous_motion,
        joint_test_receipt_directory=config.joint_test_receipt_directory,
    )
    app.register(LockController())
    app.register(SimulationController(protocol))
    app.register(RobotController(protocol))
    try:
        await protocol.open()
        yield app
    finally:
        await protocol.close()
