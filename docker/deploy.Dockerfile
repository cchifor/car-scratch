# Deploy toolbox: runs deploy/deploy.sh (ssh + rsync + git) against the Pi. Repo and ~/.ssh are mounted by compose.
FROM alpine:3.22
RUN apk add --no-cache openssh-client rsync git python3 curl tar
# Windows/macOS bind mounts show key files as world-readable, which ssh refuses: copy ~/.ssh to a private dir first.
RUN printf '%s\n' '#!/bin/sh' \
    'if [ -d /ssh-src ]; then mkdir -p /root/.ssh && cp -r /ssh-src/. /root/.ssh/ && chmod 700 /root/.ssh && chmod 600 /root/.ssh/* 2>/dev/null; fi' \
    'exec "$@"' > /usr/local/bin/deploy-entry \
 && chmod +x /usr/local/bin/deploy-entry
WORKDIR /repo
ENTRYPOINT ["/usr/local/bin/deploy-entry"]
CMD ["sh", "deploy/deploy.sh", "--dry-run"]
