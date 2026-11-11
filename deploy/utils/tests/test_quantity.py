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

"""Tests for the Kubernetes quantity parser (deploy/utils/quantity.py)."""

from decimal import Decimal

import pytest

from deploy.utils.quantity import cpu_millicores, memory_bytes, parse_quantity

pytestmark = [pytest.mark.pre_merge, pytest.mark.gpu_0, pytest.mark.unit]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("500m", Decimal("0.5")),
        ("2", Decimal(2)),
        ("1.5", Decimal("1.5")),
        ("4k", Decimal(4000)),
        ("512Mi", Decimal(512 * 1024**2)),
        ("1Gi", Decimal(1024**3)),
    ],
)
def test_parse_quantity(value, expected):
    assert parse_quantity(value) == expected


def test_cpu_millicores_rounds_up():
    assert cpu_millicores("250m") == 250
    assert cpu_millicores("0.5") == 500
    assert cpu_millicores("0.0001") == 1


def test_memory_bytes():
    assert memory_bytes("512Mi") == 536870912
    assert memory_bytes("1Gi") == 1073741824


@pytest.mark.parametrize("value", ["", "abc", "1.2.3", "5Xi", "-1"])
def test_parse_quantity_rejects_invalid(value):
    with pytest.raises(ValueError):
        parse_quantity(value)
