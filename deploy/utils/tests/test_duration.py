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

"""Tests for the duration parser (deploy/utils/duration.py)."""

import pytest

from deploy.utils.duration import parse_duration

pytestmark = [pytest.mark.pre_merge, pytest.mark.gpu_0, pytest.mark.unit]


@pytest.mark.parametrize(
    ("text", "seconds"),
    [
        ("45s", 45),
        ("5m", 300),
        ("1m30s", 90),
        ("2h", 7200),
        ("1h30m", 5400),
        ("1h0m5s", 3605),
    ],
)
def test_parse_duration(text, seconds):
    assert parse_duration(text) == seconds


@pytest.mark.parametrize("text", ["", " ", "5", "m", "1.5h", "30s1m", "1d", "-5m"])
def test_parse_duration_rejects_invalid(text):
    with pytest.raises(ValueError):
        parse_duration(text)
