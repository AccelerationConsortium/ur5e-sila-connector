class CommandRejected(Exception):
    """The requested command is unsupported or invalid in the current robot state."""


class DeviceUnavailable(Exception):
    """The robot connection is closed."""


class OutcomeUnknown(Exception):
    """A control request lost its response; inspect robot state and do not replay it."""
