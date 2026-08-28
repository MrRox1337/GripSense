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
    # Unit scales (read straight from the control table)
    # ------------------------------------------------------------------
    @property
    def position_unit_deg(self):
        return self.ct["units"]["position_deg_per_tick"]

    @property
    def current_unit_ma(self):
        return self.ct["units"]["current_ma_per_tick"]

    @property
    def velocity_unit_rev(self):
        return self.ct["units"]["velocity_rev_per_min_per_tick"]

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def close(self):
        try:
            self.disable_torque()
        finally:
            self.port_handler.closePort()

    def _check(self, comm_result, error, action):
        if comm_result != COMM_SUCCESS:
            raise IOError(f"{action} failed: {self.packet_handler.getTxRxResult(comm_result)}")
        if error != 0:
            raise IOError(f"{action} error: {self.packet_handler.getRxPacketError(error)}")

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------
    def disable_torque(self):
        with self.lock:
            comm_result, error = self.packet_handler.write1ByteTxRx(
                self.port_handler, self.dxl_id,
                self.ct["addresses"]["torque_enable"],
                self.ct["values"]["torque_disable"],
            )
        self._check(comm_result, error, "Disable torque")

    def set_operating_mode_current_based_position(self):
        # Torque must be off to change operating mode.
        self.disable_torque()
        with self.lock:
            comm_result, error = self.packet_handler.write1ByteTxRx(
                self.port_handler, self.dxl_id,
                self.ct["addresses"]["operating_mode"],
                self.ct["values"]["operating_mode_current_based_position"],
            )
        self._check(comm_result, error, "Set operating mode")

    def enable_torque(self):
        with self.lock:
            comm_result, error = self.packet_handler.write1ByteTxRx(
                self.port_handler, self.dxl_id,
                self.ct["addresses"]["torque_enable"],
                self.ct["values"]["torque_enable"],
            )
        self._check(comm_result, error, "Enable torque")

    def set_goal_current(self, current_units):
        with self.lock:
            comm_result, error = self.packet_handler.write2ByteTxRx(
                self.port_handler, self.dxl_id,
                self.ct["addresses"]["goal_current"],
                int(current_units) & 0xFFFF,
            )
        self._check(comm_result, error, "Set goal current")

    def set_profile_velocity(self, velocity_units):
        with self.lock:
            comm_result, error = self.packet_handler.write4ByteTxRx(
                self.port_handler, self.dxl_id,
                self.ct["addresses"]["profile_velocity"],
                int(velocity_units),
            )
        self._check(comm_result, error, "Set profile velocity")

    def set_goal_position(self, position_ticks):
        with self.lock:
            comm_result, error = self.packet_handler.write4ByteTxRx(
                self.port_handler, self.dxl_id,
                self.ct["addresses"]["goal_position"],
                int(position_ticks) & 0xFFFFFFFF,
            )
        self._check(comm_result, error, "Set goal position")

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------
    def read_present_position(self):
        with self.lock:
            value, comm_result, error = self.packet_handler.read4ByteTxRx(
                self.port_handler, self.dxl_id,
                self.ct["addresses"]["present_position"],
            )
        self._check(comm_result, error, "Read present position")
        return to_signed32(value)

    def read_present_current(self):
        with self.lock:
            value, comm_result, error = self.packet_handler.read2ByteTxRx(
                self.port_handler, self.dxl_id,
                self.ct["addresses"]["present_current"],
            )
        self._check(comm_result, error, "Read present current")
        return to_signed16(value)
