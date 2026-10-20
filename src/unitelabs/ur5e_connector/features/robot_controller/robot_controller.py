from unitelabs.cdk import sila

from ...io import Ur5eProtocol


class RobotController(sila.Feature):
    """Inspect robot state and control UR programs."""

    def __init__(self, protocol: Ur5eProtocol) -> None:
        super().__init__(originator="io.unitelabs", category="robot", version="1.0", maturity_level="Draft")
        self.protocol = protocol

    @sila.UnobservableProperty()
    async def get_joint_positions(self) -> list[float]:
        """
        Read six actual joint angles in radians, from Base through Wrist 3.

        Raises:
          DeviceUnavailable: If current joint positions cannot be read.
        """
        return await self.protocol.get_joint_positions()

    @sila.UnobservableCommand()
    async def move_base_positive_five_degrees(self) -> str:
        """
        Move Base positively by five degrees at at most one degree per second, then verify settling.

        Returns:
          Receipt: The measured motion receipt, or a clearly marked simulation result.

        Raises:
          CommandRejected: If the one-shot operation is disabled, already consumed or preconditions fail.
          DeviceUnavailable: If current controller state cannot be read.
          OutcomeUnknown: If actual completion cannot be confirmed. Do not replay.
        """
        return await self.protocol.move_base_positive_five_degrees()

    @sila.UnobservableCommand()
    async def move_elbow_positive_five_degrees(self) -> str:
        """
        Move Elbow positively by five degrees at at most one degree per second, then verify settling.

        Returns:
          Receipt: The measured motion receipt, or a clearly marked simulation result.

        Raises:
          CommandRejected: If the one-shot operation is disabled, already consumed or preconditions fail.
          DeviceUnavailable: If current controller state cannot be read.
          OutcomeUnknown: If actual completion cannot be confirmed. Do not replay.
        """
        return await self.protocol.move_elbow_positive_five_degrees()

    @sila.UnobservableCommand()
    async def move_joint(self, joint: str, delta_degrees: float, request_id: str) -> str:
        """
        Test one relative joint move at at most ten degrees per second, then stop.

        Args:
          Joint: Base, Shoulder, Elbow, Wrist1, Wrist2 or Wrist3.
          DeltaDegrees: Signed relative angle, magnitude between 0.5 and 10 degrees.
          RequestId: Unique ID; reuse cannot repeat a real movement.

        Returns:
          Receipt: Measured motion record, or a clearly marked simulation result.

        Raises:
          CommandRejected: If disabled, out of range, repeated or an earlier request is uncertain.
          DeviceUnavailable: If controller state or persistent records cannot be accessed.
          OutcomeUnknown: If completion cannot be confirmed. Do not retry.
        """
        return await self.protocol.move_joint(joint, delta_degrees, request_id)

    @sila.UnobservableCommand()
    async def move_joint_at_speed(
        self,
        joint: str,
        delta_degrees: float,
        speed_degrees_per_second: float,
        request_id: str,
        expected_simulation_mode: bool,
        expires_at_unix_seconds: float,
    ) -> str:
        """
        Move one joint at a selected command speed, then verify it has stopped.

        Args:
          Joint: Base, Shoulder, Elbow, Wrist1, Wrist2 or Wrist3.
          DeltaDegrees: Relative angle with magnitude from 0.5 through 10 degrees.
          SpeedDegreesPerSecond: Command speed from 1 through 40 degrees per second.
          RequestId: Unique persistent request identifier; do not reuse for another movement.
          ExpectedSimulationMode: The reviewed backend mode; a changed mode rejects the move.
          ExpiresAtUnixSeconds: Latest permitted dispatch time; expired requests never start.

        Returns:
          Receipt: Measured motion record, or a clearly marked simulation result.

        Raises:
          CommandRejected: If disabled, invalid, repeated or a previous outcome is uncertain.
          DeviceUnavailable: If the device or persistent records cannot be accessed.
          OutcomeUnknown: If completion cannot be verified. Do not retry.
        """
        return await self.protocol.move_joint_at_speed(
            joint,
            delta_degrees,
            speed_degrees_per_second,
            request_id,
            expected_simulation_mode,
            expires_at_unix_seconds,
        )

    @sila.UnobservableCommand()
    async def motion_preflight(self) -> str:
        """
        Check stationary state and joint limits without sending motion.

        Returns:
          Report: JSON checks, actual pose and separate simulation/hardware availability.

        Raises:
          CommandRejected: If the controller is not stationary.
          DeviceUnavailable: If fresh telemetry is unavailable.
        """
        return await self.protocol.motion_preflight()

    @sila.UnobservableCommand()
    async def prepare_continuous_dance(self, speed: float, duration: float, expected_simulation_mode: bool) -> str:
        """
        Preview a continuous six-axis routine and capture its start reference.

        Args:
          Speed: Peak joint-speed cap in degrees per second, 1-40.
          Duration: Nominal controller duration in seconds, 20-120.
          ExpectedSimulationMode: Reviewed backend mode.

        Returns:
          Plan: JSON trajectory preview, ranges, derivatives and reference identifier.

        Raises:
          CommandRejected: If preflight, mode, limits or derivative checks fail.
          DeviceUnavailable: If actual state cannot be read.
        """
        return await self.protocol.prepare_motion(speed, duration, expected_simulation_mode)

    @sila.UnobservableCommand()
    async def prepare_low_speed_dance(self, duration: float, expected_simulation_mode: bool) -> str:
        """
        Preview the quarter-amplitude first-test routine with a three degree/second cap.

        Args:
          Duration: Nominal controller duration in seconds, 20-120.
          ExpectedSimulationMode: Reviewed backend mode.

        Returns:
          Plan: Checked low-speed plan; does not enable or execute physical motion.

        Raises:
          CommandRejected: If startup checks or trajectory limits fail.
          DeviceUnavailable: If actual state or motion history is unavailable.
        """
        return await self.protocol.prepare_motion(3.0, duration, expected_simulation_mode, low_speed_test=True)

    @sila.UnobservableCommand()
    async def prepare_reset(
        self, checkpoint_id: str, speed: float, duration: float, expected_simulation_mode: bool
    ) -> str:
        """
        Preview an explicit smooth return to a captured reference, without moving.

        Args:
          CheckpointId: Reference captured by a dance preview in this connector session.
          Speed: Peak speed cap in degrees per second, 1-40.
          Duration: Nominal duration in seconds, 20-120.
          ExpectedSimulationMode: Reviewed backend mode.

        Returns:
          Plan: JSON reset trajectory requiring a separate explicit execution.

        Raises:
          CommandRejected: If the reference or controller checks are invalid.
          DeviceUnavailable: If actual state cannot be read.
        """
        return await self.protocol.prepare_motion(speed, duration, expected_simulation_mode, checkpoint_id)

    @sila.UnobservableCommand()
    async def run_continuous_motion(
        self,
        plan_id: str,
        request_id: str,
        expected_simulation_mode: bool,
        expires_at_unix_seconds: float,
        remote_control_confirmed: bool,
    ) -> str:
        """
        Execute one reviewed continuous dance or reset, with no automatic replay.

        Args:
          PlanId: Unexpired prepared trajectory identifier.
          RequestId: Unique request identifier for persistent reservation.
          ExpectedSimulationMode: Reviewed backend mode.
          ExpiresAtUnixSeconds: Latest permitted dispatch time.
          RemoteControlConfirmed: Operator confirmation for real mode only.

        Returns:
          Receipt: Explicit simulation result or monitored hardware receipt.

        Raises:
          CommandRejected: If disabled, expired, repeated or preflight is invalid.
          DeviceUnavailable: If telemetry or persistent records cannot be read.
          OutcomeUnknown: If physical completion cannot be verified; never retry.
        """
        return await self.protocol.execute_motion(
            plan_id, request_id, expected_simulation_mode, expires_at_unix_seconds, remote_control_confirmed
        )

    @sila.UnobservableCommand()
    async def inspect_motion_request(self, request_id: str) -> str:
        """
        Inspect an earlier motion receipt without replaying it or clearing a fault.

        Args:
          RequestId: Identifier of an earlier motion request.

        Returns:
          Receipt: Existing persisted or session receipt as JSON.

        Raises:
          CommandRejected: If the identifier is invalid or no receipt is configured.
          DeviceUnavailable: If its record cannot be read within the bounded timeout.
        """
        return await self.protocol.inspect_motion_request(request_id)

    @sila.UnobservableProperty()
    async def get_tool_pose(self) -> list[float]:
        """
        Read actual TCP position in metres and orientation as a rotation vector in radians.

        Raises:
          DeviceUnavailable: If tool telemetry is unavailable.
        """
        return (await self.protocol.read_tool_state())[0]

    @sila.UnobservableProperty()
    async def get_speed_slider_fraction(self) -> float:
        """
        Read the pendant speed fraction; it also scales the commanded movement speed.

        Raises:
          DeviceUnavailable: If speed telemetry is unavailable.
        """
        return (await self.protocol.read_tool_state())[1]

    @sila.UnobservableProperty()
    async def get_motion_controls_enabled(self) -> bool:
        """Report whether explicit joint testing is enabled in the current backend."""
        return self.protocol.simulation_mode or self.protocol.enable_joint_testing

    @sila.UnobservableProperty()
    async def get_software_version(self) -> str:
        """
        Read the controller software version.

        Raises:
          DeviceUnavailable: If the controller cannot be queried.
        """
        return await self.protocol.get_software_version()

    @sila.UnobservableProperty()
    async def get_safety_status(self) -> str:
        """
        Read the controller safety state.

        Raises:
          DeviceUnavailable: If the controller cannot be queried.
        """
        return await self.protocol.get_safety_status()

    @sila.UnobservableProperty()
    async def get_control_outcome_unknown(self) -> bool:
        """Report whether a control request requires reconciliation."""
        return self.protocol.control_outcome_unknown

    @sila.UnobservableProperty()
    async def get_robot_mode(self) -> str:
        """
        Read the robot mode.

        Raises:
          DeviceUnavailable: If the device connection is closed.
        """
        return await self.protocol.get_robot_mode()

    @sila.UnobservableProperty()
    async def get_program_state(self) -> str:
        """
        Read STOPPED, PLAYING, or PAUSED.

        Raises:
          DeviceUnavailable: If the device connection is closed.
        """
        return await self.protocol.get_program_state()

    @sila.UnobservableProperty()
    async def get_loaded_program(self) -> str:
        """
        Read the loaded program path, or an empty string.

        Raises:
          DeviceUnavailable: If the device connection is closed.
        """
        return await self.protocol.get_loaded_program()

    @sila.UnobservableProperty()
    async def get_running(self) -> bool:
        """
        Read whether a program is running.

        Raises:
          DeviceUnavailable: If the device connection is closed.
        """
        return await self.protocol.get_running()

    @sila.UnobservableCommand()
    async def load_program(self, program: str) -> None:
        """
        Load a program while stopped.

        Args:
          Program: The .urp program path in the controller.

        Raises:
          OutcomeUnknown: If a control response was lost or unrecognized.
          CommandRejected: If the path or program state is invalid.
          DeviceUnavailable: If the device connection is closed.
        """
        await self.protocol.load_program(program)

    @sila.UnobservableCommand()
    async def play(self) -> None:
        """
        Start or resume the loaded program.

        Raises:
          OutcomeUnknown: If a control response was lost or unrecognized.
          CommandRejected: If no program is loaded or it is already running.
          DeviceUnavailable: If the device connection is closed.
        """
        await self.protocol.play()

    @sila.UnobservableCommand()
    async def pause(self) -> None:
        """
        Pause the running program.

        Raises:
          OutcomeUnknown: If a control response was lost or unrecognized.
          CommandRejected: If the program is not running.
          DeviceUnavailable: If the device connection is closed.
        """
        await self.protocol.pause()

    @sila.UnobservableCommand()
    async def stop(self) -> None:
        """
        Stop the program. This is not an emergency stop.

        Raises:
          CommandRejected: If control is disabled or rejected.
          OutcomeUnknown: If a control response was lost or unrecognized.
          DeviceUnavailable: If the device connection is closed.
        """
        await self.protocol.stop()

    @sila.UnobservableCommand()
    async def send_command(self, command: str) -> str:
        """
        Send one command from the supported Dashboard subset.

        Args:
          Command: A Dashboard command such as robotmode, load demo.urp, or play.

        Returns:
          Response: The controller response.

        Raises:
          OutcomeUnknown: If a control response was lost or unrecognized.
          CommandRejected: If the command or program state is invalid.
          DeviceUnavailable: If the device connection is closed.
        """
        return await self.protocol.send_command(command)
