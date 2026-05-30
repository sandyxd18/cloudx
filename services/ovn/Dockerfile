FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
      ca-certificates \
      iproute2 \
      netcat-openbsd \
      openvswitch-switch \
      ovn-central \
      ovn-host \
    && rm -rf /var/lib/apt/lists/*

COPY --chmod=0755 start-ovn-db.sh start-northd.sh start-controller.sh /usr/local/bin/
