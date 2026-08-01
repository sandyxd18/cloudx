FROM haproxy:2.9-alpine

# Install envsubst for config templating
RUN apk add --no-cache gettext bash

COPY haproxy.cfg.template /etc/haproxy/haproxy.cfg.template
COPY start.sh /usr/local/bin/start.sh
RUN chmod +x /usr/local/bin/start.sh

ENTRYPOINT ["/usr/local/bin/start.sh"]
