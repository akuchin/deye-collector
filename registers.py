"""Register map for Deye single-phase hybrid inverters (SG01LP1 / SG03LP1, device type 3)."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Reg:
    name: str
    addr: int
    scale: float = 1.0
    offset: float = 0.0
    signed: bool = False

    def decode(self, raw: int) -> float:
        if self.signed and raw > 0x7FFF:
            raw -= 0x10000
        return round(raw * self.scale + self.offset, 3)


# Contiguous blocks read in one request each: (start address, count).
BLOCKS = [(59, 52), (150, 45)]

REGISTERS = [
    Reg("run_state", 59),  # 0 standby, 1 self-check, 2 normal, 3 alarm, 4 fault
    Reg("battery_charge_today_kwh", 70, 0.1),
    Reg("battery_discharge_today_kwh", 71, 0.1),
    Reg("grid_import_today_kwh", 76, 0.1),
    Reg("grid_export_today_kwh", 77, 0.1),
    Reg("grid_frequency_hz", 79, 0.01),
    Reg("load_today_kwh", 84, 0.1),
    Reg("dc_temp_c", 90, 0.1, -100),
    Reg("radiator_temp_c", 91, 0.1, -100),
    Reg("pv_today_kwh", 108, 0.1),
    Reg("pv1_voltage_v", 109, 0.1),
    Reg("pv1_current_a", 110, 0.1),
    Reg("grid_voltage_v", 150, 0.1),
    Reg("inverter_voltage_v", 154, 0.1),
    Reg("load_voltage_v", 157, 0.1),
    Reg("gen_power_w", 166, signed=True),  # GEN port (generator) power
    Reg("grid_power_w", 169, signed=True),  # + import from grid, - export
    Reg("inverter_power_w", 175, signed=True),
    Reg("load_power_w", 178),
    Reg("battery_temp_c", 182, 0.1, -100),
    Reg("battery_voltage_v", 183, 0.01),
    Reg("battery_soc_percent", 184),
    Reg("pv1_power_w", 186),
    Reg("pv2_power_w", 187),
    Reg("battery_power_w", 190, signed=True),  # + discharge, - charge
    Reg("battery_current_a", 191, 0.01, signed=True),
    Reg("output_frequency_hz", 192, 0.01),
    Reg("grid_relay", 194),
]

# Grid is considered present above this voltage.
GRID_UP_MIN_VOLTAGE = 180.0


def decode(raw_by_addr: dict[int, int]) -> dict[str, float]:
    values = {r.name: r.decode(raw_by_addr[r.addr]) for r in REGISTERS}
    values["pv_power_w"] = values["pv1_power_w"] + values["pv2_power_w"]
    values["grid_up"] = 1.0 if values["grid_voltage_v"] >= GRID_UP_MIN_VOLTAGE else 0.0
    return values
