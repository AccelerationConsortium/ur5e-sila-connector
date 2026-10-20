import dataclasses
from importlib.metadata import version

from unitelabs.cdk import ConnectorBaseConfig, SiLAServerConfig

__version__ = version("unitelabs-ur5e-connector")


@dataclasses.dataclass
class Ur5eConnectorConfig(ConnectorBaseConfig):
    """Configure the host-based UR5e connector."""

    simulation_mode: bool = True
    """Run without a physical robot. Disable to connect to the configured controller."""

    robot_host: str = ""
    """Controller IPv4 address or hostname. Required for real mode."""

    dashboard_port: int = 29999
    """Controller Dashboard TCP port."""

    enable_program_control: bool = False
    """Permit explicit real program commands after on-site preparation."""

    approved_program: str = ""
    """Exact reviewed /programs/*.urp path permitted for real loading and playing."""

    enable_base_step: bool = False
    """Enable the separately authorized single Base +5 degree operation."""

    base_step_receipt: str = ""
    """Exclusive receipt file for the one-shot Base move; existing files prevent replay."""

    base_step_reference: str = ""
    """Optional prior receipt binding a reconciled correction to the original absolute target."""

    enable_elbow_step: bool = False
    """Enable the separately authorized single Elbow +5 degree operation."""

    elbow_step_receipt: str = ""
    """Exclusive receipt file for the one-shot Elbow move; existing files prevent replay."""

    enable_joint_testing: bool = False
    """Permit explicit 0.5-10 degree joint tests with a reviewed speed cap of at most forty degrees per second."""

    enable_continuous_motion: bool = False
    """Enable real continuous dance/reset only after separate hardware qualification. Simulation remains available."""

    joint_test_receipt_directory: str = ""
    """Persistent request records; duplicate IDs and incomplete earlier requests prevent movement."""

    sila_server: SiLAServerConfig = dataclasses.field(
        default_factory=lambda: SiLAServerConfig(
            hostname="127.0.0.1",
            port=50052,
            name="UR5e Connector (Simulation)",
            type="Robot",
            description="Universal Robots UR5e Dashboard connector; backend selected by configuration.",
            version=str(__version__),
            vendor_url="https://unitelabs.io/",
        )
    )
