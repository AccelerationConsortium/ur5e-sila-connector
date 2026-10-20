import typing_extensions as typing

from unitelabs.cdk.features.core.simulation_controller import (
    SimulationControllerBase,
    StartRealModeFailed,
    StartSimulationModeFailed,
)

from ...io import Ur5eProtocol
from ...io.errors import CommandRejected, DeviceUnavailable


class SimulationController(SimulationControllerBase):
    """Switch between the configured controller and a simulated device while stopped."""

    def __init__(self, protocol: Ur5eProtocol) -> None:
        super().__init__()
        self.protocol = protocol

    @typing.override
    async def simulation_mode(self) -> bool:
        """Report the active backend."""
        return self.protocol.simulation_mode

    @typing.override
    async def start_simulation_mode(self) -> None:
        """
        Enter simulation while the current program is stopped.

        Raises:
          StartSimulationModeFailed: If the robot cannot be confirmed stopped.
        """
        try:
            await self.protocol.set_simulation_mode(True)
        except (CommandRejected, DeviceUnavailable) as error:
            raise StartSimulationModeFailed(str(error)) from error

    @typing.override
    async def start_real_mode(self) -> None:
        """
        Connect to the configured controller while the simulated program is stopped.

        Raises:
          StartRealModeFailed: If the configured controller cannot be reached or the program is active.
        """
        try:
            await self.protocol.set_simulation_mode(False)
        except (CommandRejected, DeviceUnavailable) as error:
            raise StartRealModeFailed(str(error)) from error
