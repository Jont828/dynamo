"""Surface verl-recipe's weight-update acknowledgements in the driver log.

Recipe main logs `[dynamo-vllm][rank=N] ... sender DONE / completed OK / kv cache cleared`
at DEBUG through a logger with no level of its own, so they never reach the driver log.
verl imports this module in the driver and in every Ray worker via VERL_USE_EXTERNAL_MODULES.
"""

import logging
import sys

_logger = logging.getLogger("recipe.dynamo.dynamo_vllm_rollout")
if not getattr(_logger, "_kit_handler", False):
    _handler = logging.StreamHandler(sys.stderr)
    _handler.setFormatter(logging.Formatter("%(message)s"))
    _logger.addHandler(_handler)
    _logger.setLevel(logging.DEBUG)
    _logger.propagate = False
    _logger._kit_handler = True
