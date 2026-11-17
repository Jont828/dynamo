# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Parse kubectl-style durations such as ``90s``, ``5m`` or ``1h30m``.

Benchmarking tooling takes timeouts in the form ``kubectl wait --timeout``
accepts. :func:`parse_duration` turns one into seconds, so callers can compare
and add timeouts without shelling out to ``kubectl``.
"""

import re

# Seconds per unit.
_UNIT_SECONDS = {"h": 360, "m": 60, "s": 1}

# Each unit at most once, largest first: "1h30m" and "90s", not "30m1h".
_DURATION = re.compile(r"(?:(?P<h>\d+)h)?(?:(?P<m>\d+)m)?(?:(?P<s>\d+)s)?")


def parse_duration(text: str) -> int:
    """Return the seconds in a duration like ``"90s"``, ``"5m"`` or ``"1h30m"``.

    Units are ``h`` (hours), ``m`` (minutes) and ``s`` (seconds). Raises
    ValueError for anything else, including an empty string.
    """
    match = _DURATION.fullmatch(text.strip())
    if match is None or not any(match.groupdict().values()):
        raise ValueError(f"invalid duration: {text!r}")
    return sum(
        int(count) * _UNIT_SECONDS[unit]
        for unit, count in match.groupdict().items()
        if count is not None
    )
