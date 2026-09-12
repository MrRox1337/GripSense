"""
Hardware driver for a Dynamixel X-series servo used as a gripper actuator.

Register addresses, register lengths, enum values and unit conversion scales
all come from a YAML control table supplied by the caller, so this module
knows nothing about where the project keeps its configuration and can be
reused as-is with a different X-series model.
"""

import threading
from pathlib import Path

import yaml
from dynamixel_sdk import (
    PortHandler,
    PacketHandler,
    COMM_SUCCESS,
)


def load_control_table(path):
    """Load a Dynamixel control table description from a YAML file."""
    with open(Path(path), "r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def to_signed16(value):
    """Convert an unsigned 16-bit register read-back into a signed value."""
    if value > 0x7FFF:
        value -= 0x10000
    return value


def to_signed32(value):
    """
    Convert an unsigned 32-bit register read-back into a signed value.

    Present Position is signed: fingers that close past tick 0 report negative
    positions, which arrive over the wire as values just under 2**32. Without
    this, -141 reads back as 4294967155 and every comparison against it - the
    settle tolerance, the calibration floor guard, the travel span - is wrong
    by four billion.
    """
    if value > 0x7FFFFFFF:
        value -= 0x100000000
    return value


class DynamixelGripper:
    """
    All reads/writes go through self.lock. The SDK's PortHandler tracks a
    single is_using flag per port; without serializing access, a poll
    thread's reads and a UI thread's writes race on that flag and the SDK
    returns COMM_PORT_BUSY ("Port is in use!" / "TxRx in use").
    """

    def __init__(self, port_name, baudrate, protocol_version, dxl_id,
                 control_table_path):
        self.dxl_id = dxl_id
        self.ct = load_control_table(control_table_path)
        self.port_handler = PortHandler(port_name)
        self.packet_handler = PacketHandler(protocol_version)
        self.lock = threading.RLock()

        if not self.port_handler.openPort():
            raise IOError(f"Failed to open port {port_name}")
        if not self.port_handler.setBaudRate(baudrate):
            self.port_handler.closePort()
            raise IOError(f"Failed to set baudrate {baudrate}")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def close(self):
        try:
            self.disable_torque()
        finally:
            self.port_handler.closePort()

    # ------------------------------------------------------------------
    # Register access
    #
    # Every public method below is one call to _write or _read. These own the
    # plumbing and nothing else: take the port lock, make the width-appropriate
    # SDK call at whatever address the control table gives for `key`, drop the
    # lock, and raise on a bad comm result or a servo error.
    #
    # What to send stays with the caller, in one visible expression, so each
    # public method still reads straight off the datasheet - register name,
    # register width, value. In particular the two's-complement masking is
    # written at the method that knows the register's sign convention, not
    # applied blanket here: a generic mask would quietly turn a negative
    # profile velocity into a maximum-speed command instead of a packet error.
    # ------------------------------------------------------------------
    def _check(self, comm_result, error, action):
        if comm_result != COMM_SUCCESS:
            raise IOError(f"{action} failed: {self.packet_handler.getTxRxResult(comm_result)}")
        if error != 0:
            raise IOError(f"{action} error: {self.packet_handler.getRxPacketError(error)}")

    def _write_fn(self, width):
        """The SDK call that writes a register `width` bytes wide."""
        if width == 1:
            return self.packet_handler.write1ByteTxRx
        if width == 2:
            return self.packet_handler.write2ByteTxRx
        if width == 4:
            return self.packet_handler.write4ByteTxRx
        raise ValueError(f"No {width}-byte write in the Dynamixel SDK")

    def _read_fn(self, width):
        """The SDK call that reads a register `width` bytes wide."""
        if width == 2:
            return self.packet_handler.read2ByteTxRx
        if width == 4:
            return self.packet_handler.read4ByteTxRx
        raise ValueError(f"No {width}-byte read in the Dynamixel SDK")

    def _write(self, key, width, value, action):
        """Write `value`, exactly as given, to the register named `key`."""
        sdk_write = self._write_fn(width)
        with self.lock:
            comm_result, error = sdk_write(
                self.port_handler, self.dxl_id,
                self.ct["addresses"][key], value,
            )
        self._check(comm_result, error, action)

    def _read(self, key, width, action):
        """Read the register named `key`, raw and unsigned, as the wire had it."""
        sdk_read = self._read_fn(width)
        with self.lock:
            value, comm_result, error = sdk_read(
                self.port_handler, self.dxl_id,
                self.ct["addresses"][key],
            )
        self._check(comm_result, error, action)
        return value

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------
    def disable_torque(self):
        self._write("torque_enable", 1,
                    self.ct["values"]["torque_disable"], "Disable torque")

    def enable_torque(self):
        self._write("torque_enable", 1,
                    self.ct["values"]["torque_enable"], "Enable torque")

    def set_operating_mode_current_based_position(self):
        # Torque must be off to change operating mode.
        self.disable_torque()
        self._write("operating_mode", 1,
                    self.ct["values"]["operating_mode_current_based_position"],
                    "Set operating mode")

    def set_goal_current(self, current_units):
        # Signed 16-bit: send the two's-complement pattern.
        self._write("goal_current", 2,
                    int(current_units) & 0xFFFF, "Set goal current")

    def set_profile_velocity(self, velocity_units):
        # Unsigned, and deliberately unmasked: a negative velocity should fail
        # in the SDK rather than wrap to the fastest speed the servo has.
        self._write("profile_velocity", 4,
                    int(velocity_units), "Set profile velocity")

    def set_goal_position(self, position_ticks):
        # Signed 32-bit: fingers that close past tick 0 are legitimate.
        self._write("goal_position", 4,
                    int(position_ticks) & 0xFFFFFFFF, "Set goal position")

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------
    def read_present_position(self):
        return to_signed32(self._read("present_position", 4, "Read present position"))

    def read_present_current(self):
        return to_signed16(self._read("present_current", 2, "Read present current"))
