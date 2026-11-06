FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git curl ca-certificates nodejs npm procps && rm -rf /var/lib/apt/lists/* \
 && useradd -m -s /bin/bash sandbox
USER sandbox
WORKDIR /home/sandbox
ENV PATH="/home/sandbox/.local/bin:${PATH}"
CMD ["sleep","infinity"]
