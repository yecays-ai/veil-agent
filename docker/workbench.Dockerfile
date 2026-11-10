# Image for the workbench (ticket 238). Build: docker build -t veil-workbench -f docker/workbench.Dockerfile docker
# Only the project folder is mounted into it at run time; no host environment variables, no capabilities.
FROM node:22-slim
LABEL veil.workbench.network-lock="1"
# Debian's system Python refuses `pip install` (PEP 668). The workbench container is disposable, so Python packages
# go into a virtualenv that is first on PATH: `pip install x`, `pip3 install x` and `python3 -m pip install x` all
# just work for the node user, with no --user / --break-system-packages variants to try.
LABEL veil.workbench.install-env="1"
RUN apt-get update && apt-get install -y --no-install-recommends git python3 python3-pip python3-venv ca-certificates \
    iptables iproute2 \
    && rm -rf /var/lib/apt/lists/* && npm install -g opencode-ai@1.19.5 \
    && python3 -m venv /opt/venv && chown -R node:node /opt/venv
ENV VIRTUAL_ENV=/opt/venv PATH=/opt/venv/bin:$PATH PIP_DISABLE_PIP_VERSION_CHECK=1
USER node
WORKDIR /work
