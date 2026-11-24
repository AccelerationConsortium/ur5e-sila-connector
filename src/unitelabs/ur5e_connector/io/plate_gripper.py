"""A parallel gripper for SBS microplates: one backend contract, simulated until hardware is chosen."""

import asyncio
import dataclasses
import enum

from .errors import CommandRejected, DeviceUnavailable

MIN_WIDTH_MM, MAX_WIDTH_MM = 60.0, 140.0
"""Finger openings a plate grip may ask for; an SBS plate is 85.48 mm by 127.76 mm."""
MIN_FORCE_N, MAX_FORCE_N = 5.0, 60.0
WIDTH_TOLERANCE_MM = 2.0
"""How far the width the fingers stop at may differ from the width asked for."""
FINGER_SPEED_MM_S = 100.0
"""Simulated closing and opening speed, so a simulated grip takes about as long as a real one."""
OVERTRAVEL_MM = 8.0
"""How far past the expected width the fingers are sent, so they stop on the plate and not before it."""


class GripperState(str, enum.Enum):
    """What the fingers are doing."""

    OPEN = "OPEN"
    HOLDING = "HOLDING"
    UNAVAILABLE = "UNAVAILABLE"


@dataclasses.dataclass
class SimulatedPlateGripper:
    """
    Fingers that close on whatever the simulation scene puts between them.

    The scene is set from outside (`set_object`): this model knows nothing about
    where the arm is, so the caller says what is at the current grasp point.
    """

    opening_mm: float = MAX_WIDTH_MM
    object_mm: float = 0.0
    holding: bool = False

    async def _travel(self, target_mm: float) -> None:
        await asyncio.sleep(abs(self.opening_mm - target_mm) / FINGER_SPEED_MM_S)
        self.opening_mm = target_mm

    async def grip(self, width_mm: float) -> float:
        """Close toward `width_mm`; return the opening the fingers stopped at."""
        before = self.opening_mm
        if 0 < self.object_mm <= before:
            await self._travel(self.object_mm)
            if abs(self.object_mm - width_mm) > WIDTH_TOLERANCE_MM:
                stopped = self.opening_mm
                await self._travel(before)
                msg = (
                    f"The fingers stopped at {stopped:.2f} mm, not the expected {width_mm:.2f} mm. "
                    "They were opened again; nothing is held."
                )
                raise CommandRejected(msg)
            self.holding = True
            return self.opening_mm
        await self._travel(max(0.0, width_mm - OVERTRAVEL_MM))
        await self._travel(before)
        msg = "The fingers closed without meeting a plate. They were opened again; nothing is held."
        raise CommandRejected(msg)

    async def release(self, opening_mm: float) -> float:
        """Open to `opening_mm`; whatever was held stays where the fingers were."""
        if self.holding and opening_mm <= self.opening_mm:
            msg = f"Open wider than the held plate ({self.opening_mm:.2f} mm) to release it."
            raise CommandRejected(msg)
        await self._travel(opening_mm)
        self.holding = False
        return self.opening_mm


class PlateGripper:
    """Serialize gripper commands and refuse them while the arm moves or in real mode."""

    MODEL_SIMULATED = "Simulated parallel plate gripper, 0-140 mm"
    MODEL_NONE = "No gripper driver configured"

    def __init__(self, protocol: object) -> None:
        self.protocol = protocol
        self.device = SimulatedPlateGripper()
        self._lock = asyncio.Lock()

    @property
    def simulation_mode(self) -> bool:
        """Whether commands reach the simulated fingers."""
        return bool(self.protocol.simulation_mode)

    @property
    def model(self) -> str:
        """The gripper commands would reach in the current mode."""
        return self.MODEL_SIMULATED if self.simulation_mode else self.MODEL_NONE

    @property
    def state(self) -> GripperState:
        """The current finger state, or UNAVAILABLE in real mode."""
        if not self.simulation_mode:
            return GripperState.UNAVAILABLE
        return GripperState.HOLDING if self.device.holding else GripperState.OPEN

    @property
    def opening_mm(self) -> float:
        """The current finger opening."""
        self._require_driver()
        return self.device.opening_mm

    def _require_driver(self) -> None:
        if not self.simulation_mode:
            msg = "No gripper driver is configured for real mode. Plate gripping runs in simulation only."
            raise DeviceUnavailable(msg)

    def _check(self, expected_mode: bool) -> None:
        if expected_mode != self.simulation_mode:
            msg = "Backend mode changed; no gripper command sent."
            raise CommandRejected(msg)
        self._require_driver()
        if self.protocol.active_motion is not None:
            msg = "Wait for the arm to stop before moving the gripper."
            raise CommandRejected(msg)

    async def grip(self, width_mm: float, force_n: float, expected_mode: bool) -> float:
        """Grip a plate of the expected width with a bounded force."""
        if not MIN_WIDTH_MM <= width_mm <= MAX_WIDTH_MM or not MIN_FORCE_N <= force_n <= MAX_FORCE_N:
            msg = (
                f"Grip width must be {MIN_WIDTH_MM:g}-{MAX_WIDTH_MM:g} mm and force {MIN_FORCE_N:g}-{MAX_FORCE_N:g} N."
            )
            raise CommandRejected(msg)
        async with self._lock:
            self._check(expected_mode)
            if self.device.holding:
                msg = "The gripper already holds a plate. Release it first."
                raise CommandRejected(msg)
            if self.device.opening_mm < width_mm + WIDTH_TOLERANCE_MM:
                msg = f"Open the fingers wider than {width_mm:.2f} mm before gripping."
                raise CommandRejected(msg)
            return await self.device.grip(width_mm)

    async def release(self, opening_mm: float, expected_mode: bool) -> float:
        """Open the fingers to the given width."""
        if not MIN_WIDTH_MM <= opening_mm <= MAX_WIDTH_MM:
            msg = f"Opening must be {MIN_WIDTH_MM:g}-{MAX_WIDTH_MM:g} mm."
            raise CommandRejected(msg)
        async with self._lock:
            self._check(expected_mode)
            return await self.device.release(opening_mm)

    async def set_simulated_object(self, width_mm: float) -> None:
        """Say what is between the simulated fingers at the current grasp point; 0 for nothing."""
        if not 0 <= width_mm <= MAX_WIDTH_MM:
            msg = f"A simulated object is 0-{MAX_WIDTH_MM:g} mm wide."
            raise CommandRejected(msg)
        async with self._lock:
            if not self.simulation_mode:
                msg = "The simulation scene can be set in simulation mode only."
                raise CommandRejected(msg)
            if self.device.holding:
                msg = "Release the held plate before changing the simulation scene."
                raise CommandRejected(msg)
            self.device.object_mm = width_mm
