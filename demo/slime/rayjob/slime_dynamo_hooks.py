"""slime hooks for running the trainer against Dynamo-managed SGLang engines."""

import os


def make_weights_readable(args, version_dir: str, rollout_engines) -> None:
    """--custom-update-weight-post-write-path hook for disk weight sync.

    safetensors writes each shard with mode 0600, so only the trainer's user (root in the slime
    image) can read it. The Dynamo SGLang workers run as uid 1000 and would fail the reload with
    FileNotFoundError; make the published checkpoint world-readable before the engines load it.
    """
    for root, dirs, files in os.walk(version_dir):
        for name in dirs:
            os.chmod(os.path.join(root, name), 0o755)
        for name in files:
            os.chmod(os.path.join(root, name), 0o644)
    os.chmod(version_dir, 0o755)
