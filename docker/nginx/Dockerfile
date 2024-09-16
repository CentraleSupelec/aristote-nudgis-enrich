FROM nginx:1.19-alpine as nginx_dev

COPY docker/nginx/default.conf.template /etc/nginx/templates/default.conf.template

HEALTHCHECK --interval=10s --timeout=3s --retries=3 CMD ["curl", "localhost"]
