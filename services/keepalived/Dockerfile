FROM alpine:3.19

RUN apk add --no-cache keepalived bash gettext iputils curl

COPY keepalived.conf.template /etc/keepalived/keepalived.conf.template
COPY start.sh /usr/local/bin/start.sh
COPY check_haproxy.sh /usr/local/bin/check_haproxy.sh
RUN chmod +x /usr/local/bin/start.sh /usr/local/bin/check_haproxy.sh

ENTRYPOINT ["/usr/local/bin/start.sh"]
