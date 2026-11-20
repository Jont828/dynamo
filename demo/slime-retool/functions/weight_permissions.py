import os


def make_weights_readable(args, version_dir, rollout_engines):
    for root, dirs, files in os.walk(version_dir):
        for name in dirs:
            os.chmod(os.path.join(root, name), 0o755)
        for name in files:
            os.chmod(os.path.join(root, name), 0o644)
    os.chmod(version_dir, 0o755)
