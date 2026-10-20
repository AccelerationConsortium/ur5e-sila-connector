import dataclasses
import enum
import pathlib

from .errors import CommandRejected


class ProgramState(str, enum.Enum):
    """Describe execution of the simulated program."""

    STOPPED = "STOPPED"
    PLAYING = "PLAYING"
    PAUSED = "PAUSED"


@dataclasses.dataclass
class SimulatedDashboard:
    """Model a small Dashboard command subset without simulating physical motion."""

    joint_positions: tuple[float, ...] = (0.0, -1.5, -1.5, 0.0, 1.5, 0.0)
    loaded_program: str = ""
    program_state: ProgramState = ProgramState.STOPPED

    def execute(self, command: str) -> str:
        """
        Execute one supported Dashboard command.

        Args:
          command: A single command without line terminators.

        Returns:
          A simulated controller response.

        Raises:
          CommandRejected: If the command or execution state is invalid.
        """
        if not command or command != command.strip() or any(c in command for c in "\r\n\x00"):
            msg = "Send exactly one non-empty command without surrounding whitespace or line terminators."
            raise CommandRejected(msg)
        if command == "PolyscopeVersion":
            return "URSoftware SIMULATED (hardware version unconfirmed)"
        if command == "safetystatus":
            return "Safetymode: NORMAL"
        if command == "robotmode":
            return "Robotmode: RUNNING"
        if command == "running":
            return f"Program running: {str(self.program_state == ProgramState.PLAYING).lower()}"
        if command == "get loaded program":
            return f"Loaded program: {self.loaded_program}" if self.loaded_program else "No program loaded"
        if command == "programState":
            return f"{self.program_state.value} {self.loaded_program}".rstrip()
        if command.startswith("load "):
            path = command[5:]
            if (
                not path
                or path != path.strip()
                or not path.endswith(".urp")
                or ".." in pathlib.PurePosixPath(path).parts
                or self.program_state != ProgramState.STOPPED
            ):
                msg = "Load requires a .urp program path without traversal and a stopped robot."
                raise CommandRejected(msg)
            self.loaded_program = path
            return f"Loading program: {path}"
        if command == "play":
            if not self.loaded_program or self.program_state == ProgramState.PLAYING:
                msg = "Play requires a loaded program that is stopped or paused."
                raise CommandRejected(msg)
            self.program_state = ProgramState.PLAYING
            return "Starting program"
        if command == "pause":
            if self.program_state != ProgramState.PLAYING:
                msg = "Pause requires a running program."
                raise CommandRejected(msg)
            self.program_state = ProgramState.PAUSED
            return "Pausing program"
        if command == "stop":
            self.program_state = ProgramState.STOPPED
            return "Stopped"
        msg = f"Unsupported simulated Dashboard command: {command}"
        raise CommandRejected(msg)
