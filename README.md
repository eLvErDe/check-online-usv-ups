# Usage

Usage: `check_power_walker_ups.py` `[-h]` `-H 10.1.2.3` `[-P 80]`
                               `[-iv @225:235 @220:240]` `[-ov @225:235 @220:240]`
                               `[-if 48:52 46:54]` `[-of 48:52 46:54]`
                               `[-ll :20 :50]` `[-tc 5:30 :40]` `[-bc 50: 25:]`
                               `[-br 60: 30:]`

Parse Power Walker UPS (www.powerwalker.com) info and return current state as a Nagios check

Optional arguments:
  * `-h`, `--help`  
    Show this help message and exit
  * `-H 10.1.2.3`, `--host 10.1.2.3`  
    IP address or hostname of the UPS
  * `-P 80`, `--port 80`  
    Port of the UPS HTTP interface
  * `-iv @225:235 @220:240`, `--input-voltage @225:235 @220:240`  
    Warning/critical thresholds using Nagios-style value for input voltage
  * `-ov @225:235 @220:240`, `--output-voltage @225:235 @220:240`  
    Warning/critical thresholds using Nagios-style value for output voltage
  * `-if 48:52 46:54`, `--input-frequency 48:52 46:54`  
    Warning/critical thresholds using Nagios-style value for input frequency
  * `-of 48:52 46:54`, `--output-frequency 48:52 46:54`  
    Warning/critical thresholds using Nagios-style value for output frequency
  * `-ll :20 :50`, `--load-level :20 :50`  
    Warning/critical thresholds using Nagios-style value for load level (0-100)
  * `-tc 5:30 :40`, `--temp-celsius 5:30 :40`  
    Warning/critical thresholds using Nagios-style value for temperature in celsius degrees
  * `-bc 50: 25:`, `--battery-capacity 50: 25:`  
    Warning/critical thresholds using Nagios-style value for battery capacity (0-100)
  * `-br 60: 30:`, `--battery-remaining 60: 30:`  
    Warning/critical thresholds using Nagios-style value for battery remaining time in minutes

# Example

```
python3 check_power_walker_ups.py  --host 10.1.2.3 --input-voltage @225:239 @220:242 --input-frequency 48:52 46:54 --load-level :20 :50 --temp-celsius '5:30' ':40' --battery-capacity 50: 25: --battery-remaining 60: 30:
```
```
OK: UPS is doing fine: in: 235.2V, 50.0Hz, load: 14%, remaining: 287min, temp: 20.0°C | input_voltage=235.2V, output_voltage=229.9V, input_frequency=50.0Hz, output_frequency=50.0Hz, output_current=1.2A, battery_capacity=100%, battery_remaining_time=287min, battery_voltage=82.0V, load_level=14%, temp_celsius=20.0°C
```

```
python3 check_power_walker.py  --host 10.1.2.3 --input-voltage @225:239 @220:242 --input-frequency 48:52 46:54 --load-level :20 :50 --temp-celsius '5:15' ':19' --battery-capacity 50: 25: --battery-remaining 500: 100:
```
```
CRITICAL: Temp C: 20.0>=19.0, Batt Remain: 287<=500.0 | input_voltage=235.6V, output_voltage=229.6V, input_frequency=50.0Hz, output_frequency=50.0Hz, output_current=1.1A, battery_capacity=100%, battery_remaining_time=287min, battery_voltage=82.0V, load_level=13%, temp_celsius=20.0°C
```
