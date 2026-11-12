# Optional offline wheels

Leave this folder empty for a normal build.

If Docker cannot reach the package index reliably, run `make wheelhouse` from the repository root. It downloads every package in
`backend/requirements.lock` into this folder using your computer's own connection. The next `docker compose build` then installs
from here and does not contact the index. The wheel files are ignored by Git.
