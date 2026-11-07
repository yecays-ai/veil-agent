# Image for the workbench (ticket 238). Build: docker build -t veil-workbench -f docker/workbench.Dockerfile docker
# Only the project folder is mounted into it at run time; no host environment variables, no capabilities.
FROM node:22-slim
RUN apt-get update && apt-get install -y --no-install-recommends git python3 python3-pip ca-certificates \
    && rm -rf /var/lib/apt/lists/* && npm install -g opencode-ai@1.19.5
RUN useradd -m -u 1000 bench
USER bench
WORKDIR /work
