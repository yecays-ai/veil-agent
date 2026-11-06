# Creates the isolated environment: internal-only docker network + allow-list proxy + sandbox container (no host mounts).
$d = Split-Path -Parent $MyInvocation.MyCommand.Path
docker rm -f cc-sbx ccsbx-proxy 2>$null | Out-Null
docker network rm ccsbx-int 2>$null | Out-Null
docker build -q -t veil-ccsbx $d
docker network create --internal ccsbx-int | Out-Null
docker run -d --name ccsbx-proxy --network bridge --memory 128m --cap-drop ALL --security-opt no-new-privileges -v "${d}\proxy.py:/proxy.py:ro" python:3.12-slim python /proxy.py | Out-Null
docker network connect --alias ccsbx-proxy ccsbx-int ccsbx-proxy
docker run -d --name cc-sbx --network ccsbx-int --memory 1g --cpus 2 --pids-limit 256 --cap-drop ALL --security-opt no-new-privileges veil-ccsbx | Out-Null
docker ps --format '{{.Names}} {{.Status}}'
