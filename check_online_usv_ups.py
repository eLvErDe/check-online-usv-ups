#!/usr/bin/python3

# This is free and unencumbered software released into the public domain.
#
# Anyone is free to copy, modify, publish, use, compile, sell, or
# distribute this software, either in source code form or as a compiled
# binary, for any purpose, commercial or non-commercial, and by any
# means.
#
# In jurisdictions that recognize copyright laws, the author or authors
# of this software dedicate any and all copyright interest in the
# software to the public domain. We make this dedication for the benefit
# of the public at large and to the detriment of our heirs and
# successors. We intend this dedication to be an overt act of
# relinquishment in perpetuity of all present and future rights to this
# software under copyright law.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
# EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
# MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
# IN NO EVENT SHALL THE AUTHORS BE LIABLE FOR ANY CLAIM, DAMAGES OR
# OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE,
# ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR
# OTHER DEALINGS IN THE SOFTWARE.
#
# For more information, please refer to <https://unlicense.org>

"""
Parse Online USV UPS (www.online-usv.de) info and return current state as a Nagios check
"""


import os
import re
import sys
import argparse
import traceback

import requests
from typing import Optional, Tuple, List


#: This script filename
SCRIPT_NAME = os.path.basename(__file__)


class NagiosArgumentParser(argparse.ArgumentParser):
    """
    Inherit from ArgumentParser but exit with Nagios code 3 (Unknown) in case of argument error
    """

    def error(self, message):
        print("UNKNOWN: Bad arguments (see --help): %s" % message)
        sys.exit(3)


class NagiosThreshold:  # pylint: disable=too-few-public-methods
    """
    Evaluate Nagios threshold, see https://nagios-plugins.org/doc/guidelines.html#THRESHOLDFORMAT
    for documentation regarding format

    :param raw: Nagios threshold as text, e.g: 10, 10:, ~:10, 10:20 or @10:20
    :type raw: str
    """

    def __init__(self, raw: str) -> None:
        assert isinstance(raw, str) and raw, "raw parameter must be a non-empty string"
        matcher = re.match(r"^(?P<is_inclusive>@?)((?P<low_boundary>(\d+(\.\d+)?|~))?:)?(?P<high_boundary>\d+(\.\d+)?)?", raw)
        assert matcher is not None, "cannot parsed threshold %s, did not match regexp" % raw
        matched = matcher.groupdict()

        self.inclusive = bool(matched["is_inclusive"])
        low_boundary = matched["low_boundary"]
        if matched["low_boundary"] is None:
            self.low_boundary: float = 0
        elif matched["low_boundary"] == "~":
            self.low_boundary = float("-inf")
        else:
            self.low_boundary = float(low_boundary)
        self.high_boundary = float(matched["high_boundary"]) if matched["high_boundary"] is not None else float("inf")

        # print("Threshold %s converted to low_boundary=%s, high_boundary=%s, inclusive=%s" % (raw, self.low_boundary, self.high_boundary, self.inclusive))

    def is_outside_boundaries(self, number: float) -> Optional[str]:
        """
        Check if given number is outside boundaries and return string representing test that failed

        :param number: Any float or integer to test against boundaries
        :type number: float
        :return: Message representing the test that failed or None of provided number is inside boundaries
        :rtype: str, optional
        """

        assert isinstance(number, (float, int)), "number parameter must be a float or int"

        if self.inclusive:
            if number < self.low_boundary:
                return "%s<%s" % (number, self.low_boundary)
            if number > self.high_boundary:
                return "%s>%s" % (number, self.high_boundary)
        else:
            if number <= self.low_boundary:
                return "%s<=%s" % (number, self.low_boundary)
            if number >= self.high_boundary:
                return "%s>=%s" % (number, self.high_boundary)

        return None


class Config:
    """
    Class representing command line config

    :param host: IP address or hostname of the UPS
    :type host: str
    :param port: Port of the UPS HTTP interface
    :type port: int
    :param input_voltage: Warning/critical thresholds using Nagios-style value for input voltage
    :type input_voltage: Optional[Tuple[NagiosThreshold, NagiosThreshold]]
    :param output_voltage: Warning/critical thresholds using Nagios-style value for output voltage
    :type output_voltage: Optional[Tuple[NagiosThreshold, NagiosThreshold]]
    :param input_frequency: Warning/critical thresholds using Nagios-style value for input frequency
    :type input_frequency: Optional[Tuple[NagiosThreshold, NagiosThreshold]]
    :param output_frequency: Warning/critical thresholds using Nagios-style value for output frequency
    :type output_frequency: Optional[Tuple[NagiosThreshold, NagiosThreshold]]
    :param load_level: Warning/critical thresholds using Nagios-style value for load level (0-100)
    :type load_level: Optional[Tuple[NagiosThreshold, NagiosThreshold]]
    :param temp_celsius: Warning/critical thresholds using Nagios-style value for temperature in celsius degrees
    :type temp_celsius: Optional[Tuple[NagiosThreshold, NagiosThreshold]]
    :param battery_capacity: Warning/critical thresholds using Nagios-style value for battery capacity (0-100)
    :type battery_capacity: Optional[Tuple[NagiosThreshold, NagiosThreshold]]
    :param battery_remaining: Warning/critical thresholds using Nagios-style value for battery remaining time in minutes
    :type battery_remaining: Optional[Tuple[NagiosThreshold, NagiosThreshold]]
    """

    # No I'm not stupid, just want to avoid dataclasses dependency to support Python 3.6 (RHEL 7) out of the box
    def __init__(
        self,
        host: str,
        port: int,
        input_voltage: Optional[Tuple[NagiosThreshold, NagiosThreshold]],
        output_voltage: Optional[Tuple[NagiosThreshold, NagiosThreshold]],
        input_frequency: Optional[Tuple[NagiosThreshold, NagiosThreshold]],
        output_frequency: Optional[Tuple[NagiosThreshold, NagiosThreshold]],
        load_level: Optional[Tuple[NagiosThreshold, NagiosThreshold]],
        temp_celsius: Optional[Tuple[NagiosThreshold, NagiosThreshold]],
        battery_capacity: Optional[Tuple[NagiosThreshold, NagiosThreshold]],
        battery_remaining: Optional[Tuple[NagiosThreshold, NagiosThreshold]],
    ) -> None:
        self.host = host
        self.port = port
        self.input_voltage = input_voltage
        self.output_voltage = output_voltage
        self.input_frequency = input_frequency
        self.output_frequency = output_frequency
        self.load_level = load_level
        self.temp_celsius = temp_celsius
        self.battery_capacity = battery_capacity
        self.battery_remaining = battery_remaining


class UpsStatus:
    """
    Class representing UPS status and config

    :param ups_type: Type (model) of the UPS, e.g: OLHV2K0 ON_LINE
    :type ups_type: str
    :param input_phase: Number of phase supported for inputs, e.g: 1
    :type input_phase: int
    :param output_phase: Number of phase supported for outputs, e.g: 1
    :type output_phase: int
    :param input_voltage: Nominal voltage for inputs, e.g: 230
    :type input_voltage: int
    :param serial_number: UPS serial number, e.g: 83222006101852
    :type serial_number: str
    :param ups_fw_version: UPS firware version, e.g: VERFW:01574.05
    :type ups_fw_version: str
    :param snmp_fw_version: SNMP module firmware version, e.g: 1.1.8
    :type snmp_fw_version: str
    :param output_voltage: Nominal voltage for outputs, e.g: 230
    :type output_voltage: int
    :param rated_va: Rated power in VA, e.g: 2000
    :type rated_va: float
    :param rated_output_voltage: Rated output voltage in volts, e.g: 230
    :type rated_output_voltage: float
    :param rated_output_frequency: Rated output frequency in hertz, e.g: 50
    :type rated_output_frequency: float
    :param rated_output_current: Rated output current in amperes, e.g: 8.0
    :type rated_output_current: float
    :param rated_battery_voltage: Rated battery voltage, e.g: 72.0
    :type rated_battery_voltage: float
    :param ups_mode: Current UPS mode (TODO FIXME => force bypass), e.g: Line Mode
    :type ups_mode: str
    :param ups_temp: Current UPS temperature in celsius degrees, e.g: 27.3
    :type ups_temp: float, optional
    :param auto_reboot: Boolean representing automatic reboot feature state (TODO FIXME wtf)
    :type auto_reboot: bool
    :param converter_mode: Boolean representing converter mode feature state (TODO FIXME wtf)
    :type converter_mode: bool
    :param eco_mode: Boolean representing ECO mode feature state (TODO FIXME wtf)
    :type eco_mode: bool
    :param bypass_when_ups_off: Boolean representing bypass when UPS is off feature state (TODO FIXME wtf)
    :type bypass_when_ups_off: bool
    :param bypass_not_allowed: Boolean representing bybass is not allowed feature state (TODO FIXME wtf)
    :type bypass_not_allowed: bool
    :param fault_type: Type of fault that is currently present (TODO FIXME: example)
    :type fault_type: str, optional
    :param ups_warning: Warning message, if there is one (TODO FIXME: example)
    :type ups_warning: str, optional
    :param battery_voltage: Current battery voltage in volts, e.g: 82.0
    :type battery_voltage: float
    :param battery_capacity: Current battery capacity in percentage, e.g: 100
    :type battery_capacity: int
    :param battery_remaining_time: Current time remaining on battery in minutes, e.g: 179
    :type battery_remaining_time: int
    :param input_frequency: Current input frequency in hertz, e.g: 50
    :type input_frequency: float
    :param input_voltage: Current input voltage in volts, e.g: 235.2
    :type input_voltage: float
    :param output_frequency: Current output frequency in hertz: e.g: 50
    :type output_frequency: float
    :param output_voltage: Current output voltage in volts, e.g: 229.5
    :type output_voltage: float
    :param output_current: Current output current in amperes: e.g: 0.6
    :type output_current: float
    :param load_level: Current output load in percentage, e.g: 7
    :type load_level: int

    TODO FIXME: Other sections
    """

    def __init__(
        self,
        *,
        ups_type: str,
        input_phase: int,
        output_phase: int,
        input_voltage: int,
        output_voltage: int,
        serial_number: str,
        ups_fw_version: str,
        snmp_fw_version: str,
        rated_va: float,
        rated_output_voltage: float,
        rated_output_frequency: float,
        rated_output_current: float,
        rated_battery_voltage: float,
        ups_mode: str,
        ups_temp: Optional[float],
        auto_reboot: bool,
        converter_mode: bool,
        eco_mode: bool,
        bypass_when_ups_off: bool,
        bypass_not_allowed: bool,
        fault_type: Optional[str],
        ups_warning: Optional[str],
        battery_voltage: float,
        battery_capacity: int,
        battery_remaining_time: int,
        cur_input_frequency: float,
        cur_input_voltage: float,
        cur_output_frequency: float,
        cur_output_voltage: float,
        cur_output_current: float,
        load_level: int,
    ) -> None:

        self.ups_type = ups_type
        self.input_phase = input_phase
        self.output_phase = output_phase
        self.input_voltage = input_voltage
        self.output_voltage = output_voltage
        self.serial_number = serial_number
        self.ups_fw_version = ups_fw_version
        self.snmp_fw_version = snmp_fw_version
        self.rated_va = rated_va
        self.rated_output_voltage = rated_output_voltage
        self.rated_output_frequency = rated_output_frequency
        self.rated_output_current = rated_output_current
        self.rated_battery_voltage = rated_battery_voltage
        self.ups_mode = ups_mode
        self.ups_temp = ups_temp
        self.auto_reboot = auto_reboot
        self.converter_mode = converter_mode
        self.eco_mode = eco_mode
        self.bypass_when_ups_off = bypass_when_ups_off
        self.bypass_not_allowed = bypass_not_allowed
        self.fault_type = fault_type
        self.ups_warning = ups_warning
        self.battery_voltage = battery_voltage
        self.battery_capacity = battery_capacity
        self.battery_remaining_time = battery_remaining_time
        self.cur_input_frequency = cur_input_frequency
        self.cur_input_voltage = cur_input_voltage
        self.cur_output_frequency = cur_output_frequency
        self.cur_output_voltage = cur_output_voltage
        self.cur_output_current = cur_output_current
        self.load_level = load_level

    @classmethod
    def from_api(cls, host: str, port: int = 80) -> "UpsStatus":
        """
        Get UPS status from API and parse result into an UpsStatus class instance

        Parsing is inspired from plain JS code seen in sys_status.html page from the UPS webinterface

        :param host: UPS web interface IP address or hostname
        :type host: str
        :param port: UPS web interface port
        :type: port: int, defaults to 80
        :return: UpsStatus named tuple representing current UPS state
        :rtype: UpsStatus
        """

        url_status = f"http://{host}:{port}/cgi-bin/realInfo.cgi"
        resp_status = requests.get(url_status, headers={"User-Agent": SCRIPT_NAME}, timeout=1)
        resp_status.raise_for_status()
        raw_status = resp_status.text.splitlines()

        url_basicinfo = f"http://{host}:{port}/cgi-bin/baseInfo.cgi"
        resp_basicinfo = requests.get(url_basicinfo, headers={"User-Agent": SCRIPT_NAME}, timeout=1)
        resp_basicinfo.raise_for_status()
        raw_basicinfo = resp_basicinfo.text.splitlines()

        # Status parsing
        (
            ups_mode_raw,
            ups_temp_raw,
            auto_reboot_raw,
            converter_mode_raw,
            eco_mode_raw,
            bypass_when_ups_off_raw,
            bypass_not_allowed_raw,
            fault_type_raw,
            ups_warning_raw,
            battery_voltage_raw,
            battery_capacity_raw,
            battery_remaining_time_raw,
            cur_input_frequency_raw,
            cur_input_voltage_raw,
            input_voltage12_raw,
            cur_output_frequency_raw,
            cur_output_voltage_raw,
            output_voltage_l1_l2_raw,
            load_level_raw,
            bypass_frequency_raw,
            bypass_vr_raw,
            bypass_v12_raw,
            input_voltage_s_raw,
            input_voltage_23_raw,
            output_voltage_s_raw,
            output_voltage_l2_l3_raw,
            load_level_s_raw,
            bypass_voltage_s_raw,
            bypass_v23_raw,
            input_voltage_t_raw,
            input_voltage_31_raw,
            output_voltage_t_raw,
            output_voltage_l1_l3_raw,
            load_level_t_raw,
            bypass_voltage_t_raw,
            bypass_v31_raw,
            cur_output_current_raw,
            output_current_s_raw,
            output_current_t_raw,
            temperature_raw,
            humidity_raw,
            alarm1_raw,
            alarm2_raw,
            unused_idx_43_raw,
            unused_idx_44_raw,
            unused_idx_45_raw,
            output_apparent_power_raw,
            output_active_power_raw,
            battery_charge_current_raw,
            battery_discharge_current_raw,
            *leftovers_status,
        ) = raw_status

        assert not leftovers_status, "some additional fields (%d): %s were found, status parsing needs update" % (
            len(leftovers_status),
            ",".join(leftovers_status),
        )

        assert ups_mode_raw, "ups_mode_raw must be a non-empty string"
        assert "-" in ups_temp_raw or ups_temp_raw.isdigit(), "ups_temp_raw must either contains - or be digits, got %s" % ups_temp_raw
        assert auto_reboot_raw in ["0", "1"], "auto_reboot_raw must be either 0 or 1, got %s" % auto_reboot_raw
        assert converter_mode_raw in ["0", "1"], "converter_mode_raw must be either 0 or 1, got %s" % converter_mode_raw
        assert eco_mode_raw in ["0", "1"], "eco_mode_raw must be either 0 or 1, got %s" % eco_mode_raw
        assert bypass_when_ups_off_raw in ["0", "1"], "bypass_when_ups_off_raw must be either 0 or 1, got %s" % bypass_when_ups_off_raw
        assert bypass_not_allowed_raw in ["0", "1"], "bypass_not_allowed_raw must be either 0 or 1, got %s" % bypass_not_allowed_raw
        assert battery_voltage_raw.isdigit(), "battery_voltage_raw must be digits, got %s" % battery_voltage_raw
        assert battery_capacity_raw.isdigit(), "battery_capacity_raw must be digits, got %s" % battery_capacity_raw
        assert 0 <= int(battery_capacity_raw) <= 100, "battery_capacity_raw must be between 0 and 100, got %s" % battery_capacity_raw
        assert battery_remaining_time_raw.isdigit(), "battery_remaining_time_raw must be digits, got %s" % battery_remaining_time_raw
        assert cur_input_frequency_raw.isdigit(), "cur_input_frequency_raw must be digits, got %s" % cur_input_frequency_raw
        assert cur_input_voltage_raw.isdigit(), "cur_input_voltage_raw must be digits, got %s" % cur_input_voltage_raw
        assert input_voltage12_raw.isdigit(), "input_voltage12_raw must be digits, got %s" % input_voltage12_raw
        assert input_voltage12_raw == "0", "input_voltage12_raw must be 0, please implement support for this value, got %s" % input_voltage12_raw
        assert cur_output_frequency_raw.isdigit(), "cur_output_frequency_raw must be digits, got %s" % cur_output_frequency_raw
        assert cur_output_voltage_raw.isdigit(), "cur_output_voltage_raw must be digits, got %s" % cur_output_voltage_raw
        assert load_level_raw.isdigit(), "load_level_raw must be digits, got %s" % load_level_raw
        assert 0 <= int(load_level_raw) <= 100, "load_level_raw must be between 0 and 100, got %s" % load_level_raw
        assert cur_output_current_raw.isdigit(), "cur_output_current_raw must be digits, got %s" % cur_output_current_raw

        # Unused variables
        for unused_var_name, unused_var_raw in {
            "output_voltage_l1_l2_raw": output_voltage_l1_l2_raw,
            "bypass_vr_raw": bypass_vr_raw,
            "bypass_v12_raw": bypass_v12_raw,
            "input_voltage_s_raw": input_voltage_s_raw,
            "input_voltage_23_raw": input_voltage_23_raw,
            "output_voltage_s_raw": output_voltage_s_raw,
            "output_voltage_l2_l3_raw": output_voltage_l2_l3_raw,
            "load_level_s_raw": load_level_s_raw,
            "bypass_voltage_s_raw": bypass_voltage_s_raw,
            "bypass_v23_raw": bypass_v23_raw,
            "input_voltage_t_raw": input_voltage_t_raw,
            "input_voltage_31_raw": input_voltage_31_raw,
            "output_voltage_t_raw": output_voltage_t_raw,
            "output_voltage_l1_l3_raw": output_voltage_l1_l3_raw,
            "load_level_t_raw": load_level_t_raw,
            "bypass_voltage_t_raw": bypass_voltage_t_raw,
            "bypass_v31_raw": bypass_v31_raw,
            "output_current_s_raw": output_current_s_raw,
            "output_current_t_raw": output_current_t_raw,
            "temperature_raw": temperature_raw,
            "humidity_raw": humidity_raw,
            "alarm1_raw": alarm1_raw,
            "alarm2_raw": alarm2_raw,
            "unused_idx_43_raw": unused_idx_43_raw,
            "unused_idx_44_raw": unused_idx_44_raw,
            "unused_idx_45_raw": unused_idx_45_raw,
            "output_apparent_power_raw": output_apparent_power_raw,
            "output_active_power_raw": output_active_power_raw,
            "battery_charge_current_raw": battery_charge_current_raw,
            "battery_discharge_current_raw": battery_discharge_current_raw,
        }.items():

            assert unused_var_raw.isdigit(), "%s must be digits, got %s" % (unused_var_name, unused_var_raw)

        # Basic Info parsing
        (
            unused_idx_0_raw,
            ups_type_raw,
            input_output_phase_raw,
            input_output_voltage_raw,
            ups_serial_number_raw,
            ups_fw_version_raw,
            battery_group_number_raw,
            rated_va_raw,
            rated_output_voltage_raw,
            rated_output_frequency_raw,
            rated_output_current_raw,
            rated_battery_voltage_raw,
            snmp_fw_version_raw,
            equip_attached_raw,
            unused_idx_14_raw,
            *leftovers_basicinfo,
        ) = raw_basicinfo

        assert not leftovers_basicinfo, "some additional fields (%d): %s were found, basicinfo parsing needs update" % (
            len(leftovers_basicinfo),
            ",".join(leftovers_basicinfo),
        )

        assert ups_type_raw, "ups_type_raw must be a non-empty string"
        input_phase_raw, output_phase_raw = input_output_phase_raw.split("/")
        assert input_phase_raw.isdigit(), "input_phase_raw must be digits, got %s" % input_phase_raw
        assert output_phase_raw.isdigit(), "output_phase_raw must be digits, got %s" % output_phase_raw
        input_voltage_raw, output_voltage_raw = input_output_voltage_raw.split("/")
        assert input_voltage_raw.isdigit(), "input_voltage_raw must be digits, got %s" % input_voltage_raw
        assert output_voltage_raw.isdigit(), "output_voltage_raw must be digits, got %s" % output_voltage_raw
        assert ups_fw_version_raw, "ups_fw_version_raw must be a non-empty string"
        assert snmp_fw_version_raw, "snmp_fw_version_raw must be a non-empty string"
        assert equip_attached_raw, "equip_attached_raw must be a non-empty string"
        assert battery_group_number_raw.isdigit(), "battery_group_number_raw must be digits, got %s" % battery_group_number_raw
        assert rated_va_raw.isdigit(), "rated_va_raw must be digits, got %s" % rated_va_raw
        assert rated_output_voltage_raw.isdigit(), "rated_output_voltage_raw must be digits, got %s" % rated_output_voltage_raw
        assert rated_output_frequency_raw.isdigit(), "rated_output_frequency_raw must be digits, got %s" % rated_output_frequency_raw
        assert rated_output_current_raw.isdigit(), "rated_output_current_raw must be digits, got %s" % rated_output_current_raw
        assert rated_battery_voltage_raw.isdigit(), "rated_battery_voltage_raw must be digits, got %s" % rated_battery_voltage_raw

        return cls(
            ups_type=ups_type_raw,
            input_phase=int(input_phase_raw),
            output_phase=int(output_phase_raw),
            input_voltage=int(input_voltage_raw),
            output_voltage=int(output_voltage_raw),
            ups_fw_version=ups_fw_version_raw,
            serial_number=ups_serial_number_raw,
            snmp_fw_version=snmp_fw_version_raw,
            rated_va=float(int(rated_va_raw) / 10),
            rated_output_voltage=float(int(rated_output_voltage_raw) / 10),
            rated_output_frequency=float(int(rated_output_frequency_raw) / 10),
            rated_output_current=float(int(rated_output_current_raw) / 10),
            rated_battery_voltage=float(int(rated_battery_voltage_raw) / 10),
            ups_mode=ups_mode_raw,
            ups_temp=None if "-" in ups_temp_raw else float(int(ups_temp_raw) / 10),
            auto_reboot=auto_reboot_raw == "1",
            converter_mode=converter_mode_raw == "1",
            eco_mode=eco_mode_raw == "1",
            bypass_when_ups_off=bypass_when_ups_off_raw == "1",
            bypass_not_allowed=bypass_not_allowed_raw == "1",
            fault_type=fault_type_raw if fault_type_raw else None,
            ups_warning=ups_warning_raw if ups_warning_raw else None,
            battery_voltage=float(int(battery_voltage_raw) / 10),
            battery_capacity=int(battery_capacity_raw),
            battery_remaining_time=int(battery_remaining_time_raw),
            cur_input_frequency=float(int(cur_input_frequency_raw) / 10),
            cur_input_voltage=float(int(cur_input_voltage_raw) / 10),
            cur_output_frequency=float(int(cur_output_frequency_raw) / 10),
            cur_output_voltage=float(int(cur_output_voltage_raw) / 10),
            cur_output_current=float(int(cur_output_current_raw) / 10),
            load_level=int(load_level_raw),
        )

    def evaluate_thresholds(self, config: Config) -> Tuple[int, str]:
        """
        Evaluate thresholds against config object and return two elements tuple, first one is retcode, being 0 for OK,
        1 for WARNING, 2 for CRITICAL, second elements is a status output line with important values

        :param config: Config object with thresholds
        :type config: Config
        :return: Two elements tuple with return code and status message
        :rtype: tuple
        """

        warning_reasons: List[str] = []
        critical_reasons: List[str] = []

        # UPS is faulty
        if self.fault_type is not None:
            warning_reasons.append("UPS fault: %s" % self.fault_type)

        # UPS has warning
        if self.ups_warning:
            warning_reasons.append("Warning: %s" % self.ups_warning)

        # UPS is not online
        if self.ups_mode != "Line Mode":
            warning_reasons.append("UPS mode: %s" % self.ups_mode)

        # Voltage
        if config.input_voltage:
            input_voltage_warning_reason = config.input_voltage[0].is_outside_boundaries(self.cur_input_voltage)
            input_voltage_critical_reason = config.input_voltage[1].is_outside_boundaries(self.cur_input_voltage)
            if input_voltage_critical_reason:
                critical_reasons.append("Input Volt: " + input_voltage_critical_reason)
            elif input_voltage_warning_reason:
                warning_reasons.append("Input Volt: " + input_voltage_warning_reason)
        if config.output_voltage:
            output_voltage_warning_reason = config.output_voltage[0].is_outside_boundaries(self.cur_output_voltage)
            output_voltage_critical_reason = config.output_voltage[1].is_outside_boundaries(self.cur_output_voltage)
            if output_voltage_critical_reason:
                critical_reasons.append("Output Volt: " + output_voltage_critical_reason)
            elif output_voltage_warning_reason:
                warning_reasons.append("Output Volt: " + output_voltage_warning_reason)

        # Frequency
        if config.input_frequency:
            input_frequency_warning_reason = config.input_frequency[0].is_outside_boundaries(self.cur_input_frequency)
            input_frequency_critical_reason = config.input_frequency[1].is_outside_boundaries(self.cur_input_frequency)
            if input_frequency_critical_reason:
                critical_reasons.append("Input Freq: " + input_frequency_critical_reason)
            elif input_frequency_warning_reason:
                warning_reasons.append("Input Freq: " + input_frequency_warning_reason)
        if config.output_frequency:
            output_frequency_warning_reason = config.output_frequency[0].is_outside_boundaries(self.cur_output_frequency)
            output_frequency_critical_reason = config.output_frequency[1].is_outside_boundaries(self.cur_output_frequency)
            if output_frequency_critical_reason:
                critical_reasons.append("Output Freq: " + output_frequency_critical_reason)
            elif output_frequency_warning_reason:
                warning_reasons.append("Output Freq: " + output_frequency_warning_reason)

        # Load level
        if config.load_level:
            load_level_warning_reason = config.load_level[0].is_outside_boundaries(self.load_level)
            load_level_critical_reason = config.load_level[1].is_outside_boundaries(self.load_level)
            if load_level_critical_reason:
                critical_reasons.append("Load Level: " + load_level_critical_reason)
            elif load_level_warning_reason:
                warning_reasons.append("Load Level: " + load_level_warning_reason)

        # Temp celsius
        if config.temp_celsius and self.ups_temp is not None:
            temp_celsius_warning_reason = config.temp_celsius[0].is_outside_boundaries(self.ups_temp)
            temp_celsius_critical_reason = config.temp_celsius[1].is_outside_boundaries(self.ups_temp)
            if temp_celsius_critical_reason:
                critical_reasons.append("Temp C: " + temp_celsius_critical_reason)
            elif temp_celsius_warning_reason:
                warning_reasons.append("Temp C: " + temp_celsius_warning_reason)

        # Battery capacity
        if config.battery_capacity:
            battery_capacity_warning_reason = config.battery_capacity[0].is_outside_boundaries(self.battery_capacity)
            battery_capacity_critical_reason = config.battery_capacity[1].is_outside_boundaries(self.battery_capacity)
            if battery_capacity_critical_reason:
                critical_reasons.append("Batt Cap: " + battery_capacity_critical_reason)
            elif battery_capacity_warning_reason:
                warning_reasons.append("Batt Cap: " + battery_capacity_warning_reason)

        # Battery remaining
        if config.battery_remaining:
            battery_remaining_warning_reason = config.battery_remaining[0].is_outside_boundaries(self.battery_remaining_time)
            battery_remaining_critical_reason = config.battery_remaining[1].is_outside_boundaries(self.battery_remaining_time)
            if battery_remaining_critical_reason:
                critical_reasons.append("Batt Remain: " + battery_remaining_critical_reason)
            elif battery_remaining_warning_reason:
                warning_reasons.append("Batt Remain: " + battery_remaining_warning_reason)

        if critical_reasons:
            return 2, "CRITICAL: %s" % ", ".join(critical_reasons + warning_reasons)
        if warning_reasons:
            return 1, "WARNING: %s" % ", ".join(warning_reasons)
        else:
            return 0, "OK: UPS is doing fine: in: %.1fV, %.1fHz, load: %d%%, remaining: %dmin, temp: %s°C" % (
                self.cur_input_voltage,
                self.cur_input_frequency,
                self.load_level,
                self.battery_remaining_time,
                self.ups_temp,
            )

    def perfdata(self) -> str:
        """
        Compute Nagios perfdata

        :return: Nagios-style performance data
        :rtype: str
        """

        perfdata_r: List[str] = []
        perfdata_r.append("input_voltage=%.1fV" % self.cur_input_voltage)
        perfdata_r.append("output_voltage=%.1fV" % self.cur_output_voltage)
        perfdata_r.append("input_frequency=%.1fHz" % self.cur_input_frequency)
        perfdata_r.append("output_frequency=%.1fHz" % self.cur_output_frequency)
        perfdata_r.append("output_current=%.1fA" % self.cur_output_current)
        perfdata_r.append("battery_capacity=%d%%" % self.battery_capacity)
        perfdata_r.append("battery_remaining_time=%dmin" % self.battery_remaining_time)
        perfdata_r.append("battery_voltage=%.1fV" % self.battery_voltage)
        perfdata_r.append("load_level=%d%%" % self.load_level)
        perfdata_r.append("temp_celsius=%s" % ("" if self.ups_temp is None else "%.1f°C" % self.ups_temp))

        return ", ".join(perfdata_r)


def nagios_threshold(val: str) -> NagiosThreshold:
    """
    Custom type for argparse return a NagiosThreshold instance to handle nagios-style thresholds

    :param val: Input value to convert into NagiosThreshold
    :type val: str
    :raises argparse.ArgumentTypeError: If provided string does not match nagios-style threshold format
    :return: NagiosThreshold instance
    :rtype: tuple
    """

    try:
        threshold = NagiosThreshold(val)
        return threshold
    except Exception as exc:  # pylint: disable=broad-except
        raise argparse.ArgumentTypeError("Invalid Nagios threshold: %s" % exc) from None


def parse_args() -> Config:
    """
    Parse command line arguments and return object representing all properties

    :return: Typed object representing all command line arguments and their values
    :rtype: Config
    """

    argparser = NagiosArgumentParser(description=__doc__.strip())
    argparser.add_argument("-H", "--host", type=str, required=True, help="IP address or hostname of the UPS", metavar="10.1.2.3")
    argparser.add_argument("-P", "--port", type=int, default=80, help="Port of the UPS HTTP interface", metavar="80")
    argparser.add_argument(
        "-iv",
        "--input-voltage",
        type=nagios_threshold,
        nargs=2,
        help="Warning/critical thresholds using Nagios-style value for input voltage",
        metavar=("@225:235", "@220:240"),
    )
    argparser.add_argument(
        "-ov",
        "--output-voltage",
        type=nagios_threshold,
        nargs=2,
        help="Warning/critical thresholds using Nagios-style value for output voltage",
        metavar=("@225:235", "@220:240"),
    )
    argparser.add_argument(
        "-if",
        "--input-frequency",
        type=nagios_threshold,
        nargs=2,
        help="Warning/critical thresholds using Nagios-style value for input frequency",
        metavar=("48:52", "46:54"),
    )
    argparser.add_argument(
        "-of",
        "--output-frequency",
        type=nagios_threshold,
        nargs=2,
        help="Warning/critical thresholds using Nagios-style value for output frequency",
        metavar=("48:52", "46:54"),
    )
    argparser.add_argument(
        "-ll",
        "--load-level",
        type=nagios_threshold,
        nargs=2,
        help="Warning/critical thresholds using Nagios-style value for load level (0-100)",
        metavar=(":20", ":50"),
    )
    argparser.add_argument(
        "-tc",
        "--temp-celsius",
        type=nagios_threshold,
        nargs=2,
        help="Warning/critical thresholds using Nagios-style value for temperature in celsius degrees",
        metavar=("5:30", ":40"),
    )
    argparser.add_argument(
        "-bc",
        "--battery-capacity",
        type=nagios_threshold,
        nargs=2,
        help="Warning/critical thresholds using Nagios-style value for battery capacity (0-100)",
        metavar=("50:", "25:"),
    )
    argparser.add_argument(
        "-br",
        "--battery-remaining",
        type=nagios_threshold,
        nargs=2,
        help="Warning/critical thresholds using Nagios-style value for battery remaining time in minutes",
        metavar=("60:", "30:"),
    )
    args = argparser.parse_args()

    config = Config(**vars(args))

    return config


if __name__ == "__main__":

    try:
        CONFIG = parse_args()
        STATUS = UpsStatus.from_api(host=CONFIG.host, port=CONFIG.port)
        PERFDATA = STATUS.perfdata()
        RETCODE, MESSAGE = STATUS.evaluate_thresholds(CONFIG)
    except Exception as exc:  # pylint: disable=broad-except
        print("UNKNOWN: Check crashed: %s: %s" % (exc.__class__.__name__, exc))
        print(traceback.format_exc())
        sys.exit(3)
    else:
        print(MESSAGE + " | " + PERFDATA)
        sys.exit(RETCODE)
