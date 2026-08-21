FROM postgres:16

COPY entrypoint.sh /usr/local/bin/cloudx-postgres-entrypoint.sh
RUN chmod +x /usr/local/bin/cloudx-postgres-entrypoint.sh

ENTRYPOINT ["/usr/local/bin/cloudx-postgres-entrypoint.sh"]
