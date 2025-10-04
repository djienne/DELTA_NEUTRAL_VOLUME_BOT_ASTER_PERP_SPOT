#!/usr/bin/env python3
"""
Shared utility functions for the delta-neutral funding rate farming bot.
"""

import math


def truncate(value: float, precision: int) -> float:
    """
    Truncates a float to a given precision without rounding.

    Args:
        value: The float value to truncate
        precision: Number of decimal places to keep

    Returns:
        Truncated float value

    Example:
        >>> truncate(1.23456, 2)
        1.23
        >>> truncate(1.23456, 0)
        1.0
    """
    if precision < 0:
        precision = 0
    if precision == 0:
        return math.floor(value)
    factor = 10.0 ** precision
    return math.floor(value * factor) / factor