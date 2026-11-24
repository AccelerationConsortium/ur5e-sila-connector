import typing

from unitelabs.cdk import sila
from unitelabs.cdk.sila import constraints

from ...io.plate_gripper import MAX_FORCE_N, MAX_WIDTH_MM, MIN_FORCE_N, MIN_WIDTH_MM, PlateGripper

_MILLIMETRE = constraints.Unit("mm", [constraints.UnitComponent(constraints.SIUnit.METER, 1)], factor=0.001)
_NEWTON = constraints.Unit(
    "N",
    [
        constraints.UnitComponent(constraints.SIUnit.KILOGRAM, 1),
        constraints.UnitComponent(constraints.SIUnit.METER, 1),
        constraints.UnitComponent(constraints.SIUnit.SECOND, -2),
    ],
)
PlateWidth = typing.Annotated[
    float, constraints.MinimalInclusive(MIN_WIDTH_MM), constraints.MaximalInclusive(MAX_WIDTH_MM), _MILLIMETRE
]
GripForce = typing.Annotated[
    float, constraints.MinimalInclusive(MIN_FORCE_N), constraints.MaximalInclusive(MAX_FORCE_N), _NEWTON
]
SceneWidth = typing.Annotated[
    float, constraints.MinimalInclusive(0), constraints.MaximalInclusive(MAX_WIDTH_MM), _MILLIMETRE
]


class PlateGripperController(sila.Feature):
    """
    Grip and release SBS microplates with a parallel gripper on the arm's flange.

    The gripper runs in simulation only until a hardware driver is configured;
    real mode refuses every command. It never moves the arm.
    """

    def __init__(self, gripper: PlateGripper) -> None:
        super().__init__(originator="io.unitelabs", category="robot", version="1.0", maturity_level="Draft")
        self.gripper = gripper

    @sila.UnobservableCommand()
    async def grip_plate(
        self, width_millimeters: PlateWidth, force_newtons: GripForce, expected_simulation_mode: bool
    ) -> float:
        """
        Close the fingers on a plate of the expected width while the arm is stationary.

        Args:
          WidthMillimeters: Plate width across the fingers; 85.48 for an SBS plate gripped on its long sides.
          ForceNewtons: Grip force limit.
          ExpectedSimulationMode: Reviewed backend mode; a change rejects the command.

        Returns:
          OpeningMillimeters: The opening the fingers stopped at on the plate.

        Raises:
          CommandRejected: If no plate is met, its width differs, a plate is already held or the arm is moving.
          DeviceUnavailable: If no gripper driver is configured for the current mode.
        """
        return await self.gripper.grip(width_millimeters, force_newtons, expected_simulation_mode)

    @sila.UnobservableCommand()
    async def release_plate(self, opening_millimeters: PlateWidth, expected_simulation_mode: bool) -> float:
        """
        Open the fingers, leaving a held plate where it is.

        Args:
          OpeningMillimeters: Opening to stop at; wider than the held plate.
          ExpectedSimulationMode: Reviewed backend mode; a change rejects the command.

        Returns:
          OpeningMillimeters: The opening reached.

        Raises:
          CommandRejected: If the opening would not free the plate or the arm is moving.
          DeviceUnavailable: If no gripper driver is configured for the current mode.
        """
        return await self.gripper.release(opening_millimeters, expected_simulation_mode)

    @sila.UnobservableCommand()
    async def set_simulated_object(self, width_millimeters: SceneWidth) -> None:
        """
        Tell the simulated gripper what lies between its fingers at the current grasp point.

        Args:
          WidthMillimeters: Width of the object across the fingers; 0 when the grasp point is empty.

        Raises:
          CommandRejected: If the connector is in real mode or a plate is held.
        """
        await self.gripper.set_simulated_object(width_millimeters)

    @sila.UnobservableProperty()
    async def get_gripper_state(self) -> str:
        """Report OPEN, HOLDING, or UNAVAILABLE when no driver is configured for the current mode."""
        return self.gripper.state.value

    @sila.UnobservableProperty()
    async def get_opening_millimeters(self) -> float:
        """
        Read the current finger opening in millimetres.

        Raises:
          DeviceUnavailable: If no gripper driver is configured for the current mode.
        """
        return self.gripper.opening_mm

    @sila.UnobservableProperty()
    async def get_gripper_model(self) -> str:
        """Name the gripper that commands reach in the current mode."""
        return self.gripper.model
