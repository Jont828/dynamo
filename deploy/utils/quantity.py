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
"""Parse Kubernetes resource quantities such as ``500m``, ``2``, ``512Mi`` or ``1G``.

Benchmarking tooling uses these helpers to compare requested CPU and memory with
node capacity without shelling out to ``kubectl``. As in Kubernetes, binary
suffixes (``Ki``, ``Mi``, ``Gi``, ``Ti``) are powers of 1024, decimal suffixes
(``k``, ``M``, ``G``, ``T``) are powers of 1000, and ``m`` is one thousandth.
"""

import math
import re
from decimal import Decimal

_SUFFIXES = {
    "m": Decimal("0.001"),
    "": Decimal(1),
    "k": Decimal(1000),
    "M": Decimal(1000) ** 2,
    "G": Decimal(1000) ** 3,
    "T": Decimal(1000) ** 4,
    "Ki": Decimal(1024),
    "Mi": Decimal(1024) ** 2,
    "Gi": Decimal(1024) ** 3,
    "Ti": Decimal(1024) ** 4,
}

_QUANTITY = re.compile(r"(?P<number>\d+(?:\.\d+)?|\.\d+)(?P<suffix>[A-Za-z]*)")


def parse_quantity(value: str | int) -> Decimal:
    """Return a Kubernetes quantity as a Decimal, for example ``"500m"`` -> ``0.5``."""
    match = _QUANTITY.fullmatch(str(value).strip())
    if match is None or match["suffix"] not in _SUFFIXES:
        raise ValueError(f"not a Kubernetes quantity: {value!r}")
    return Decimal(match["number"]) * _SUFFIXES[match["suffix"]]


def cpu_millicores(value: str | int) -> int:
    """Return a CPU quantity in millicores, rounding up (``"0.5"`` -> ``500``)."""
    return math.ceil(parse_quantity(value) * 1000)


def memory_bytes(value: str | int) -> int:
    """Return a memory quantity in bytes, rounding up (``"512Mi"`` -> ``536870912``)."""
    return math.ceil(parse_quantity(value))
