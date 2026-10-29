FROM python:3.13

ENV PYTHONUNBUFFERED=1

WORKDIR /app/

# Install uv
# Ref: https://docs.astral.sh/uv/guides/integration/docker/#installing-uv
# Same uv line as the one that wrote uv.lock (`revision = 3`); keep them in step when upgrading
COPY --from=ghcr.io/astral-sh/uv:0.9.13 /uv /uvx /bin/

# Place executables in the environment at the front of the path
# Ref: https://docs.astral.sh/uv/guides/integration/docker/#using-the-environment
ENV PATH="/app/.venv/bin:$PATH"

# Compile bytecode
# Ref: https://docs.astral.sh/uv/guides/integration/docker/#compiling-bytecode
ENV UV_COMPILE_BYTECODE=1

# uv Cache
# Ref: https://docs.astral.sh/uv/guides/integration/docker/#caching
ENV UV_LINK_MODE=copy

# Install dependencies
# Ref: https://docs.astral.sh/uv/guides/integration/docker/#intermediate-layers
RUN --mount=type=cache,target=/root/.cache/uv \
  --mount=type=bind,source=legacy/whatsapp/uv.lock,target=uv.lock \
  --mount=type=bind,source=legacy/whatsapp/pyproject.toml,target=pyproject.toml \
  uv sync --frozen --no-install-project

ENV PYTHONPATH=/app

# The build context is the repo root (as for every service in this monorepo), so source paths start with legacy/whatsapp/
COPY legacy/whatsapp/pyproject.toml legacy/whatsapp/uv.lock /app/

COPY legacy/whatsapp/src /app/src/

# Sync the project (--frozen: install exactly what uv.lock says, never re-resolve at deploy time)
# Ref: https://docs.astral.sh/uv/guides/integration/docker/#intermediate-layers
RUN --mount=type=cache,target=/root/.cache/uv \
  uv sync --frozen

# Run the application
CMD ["python", "src/ansari_whatsapp/app/main.py"]