"""Build-time check: every requirement declared in pyproject.toml (runtime and dev) is installed at a version that satisfies it.
Fails the image build otherwise, so a stale lock file can never produce an image that is missing a dependency."""
import sys
import tomllib
from importlib.metadata import PackageNotFoundError, version

from packaging.requirements import Requirement

project = tomllib.load(open("pyproject.toml", "rb"))
declared = project["project"]["dependencies"] + project["project"]["optional-dependencies"]["dev"] + project["build-system"]["requires"]
problems = []
for text in declared:
    req = Requirement(text)
    try:
        installed = version(req.name)
    except PackageNotFoundError:
        problems.append(f"{text}: not installed")
        continue
    if not req.specifier.contains(installed, prereleases=True):
        problems.append(f"{text}: installed {installed}")
if problems:
    sys.exit("requirements.lock does not satisfy pyproject.toml (run `make lock`):\n  " + "\n  ".join(problems))
print(f"deps: all {len(declared)} requirements declared in pyproject.toml are satisfied.")
