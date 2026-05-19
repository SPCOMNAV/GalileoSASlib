"""
Constants for Galileo E1 and E6 signal correlation.
"""

# E6 Band Parameters
E6_CHIP_RATE_HZ = 5.115e6
E6_CODE_LENGTH_CHIPS = 5115
E6_CARRIER_HZ = 1278.75e6

# E1 Band Parameters
E1_CHIP_RATE_HZ = 1.023e6
E1_CODE_LENGTH_CHIPS = 4092
E1_CARRIER_HZ = 1575.42e6

# Carrier frequency aliases
F_CARRIER_E1 = E1_CARRIER_HZ
F_CARRIER_E6 = E6_CARRIER_HZ

# Speed of light [m/s]
SPEED_OF_LIGHT = 299792458.0

# Code periods
E6_CODE_PERIOD_MS = 1.0   # E6B/E6C: 1 ms
E1_CODE_PERIOD_MS = 4.0   # E1B/E1C: 4 ms
