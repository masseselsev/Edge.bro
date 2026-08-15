# Build stage
#
# Pinned to a major, not a floating tag: an image that silently changes its
# toolchain between two builds of the same commit is not reproducible, and the
# base moving out from under us is how this project ended up shipping packages
# built for the wrong Debian release.
FROM node:24-alpine AS build
WORKDIR /app
COPY package*.json ./
# `npm ci` and not `npm install`: install may update package-lock.json, so the
# lockfile stops describing what shipped. ci fails instead, which is the point
# of having a lockfile at all.
RUN npm ci
COPY . .
RUN npm run build

# Production stage
# 1.30 is the current nginx stable branch (even minors are stable).
FROM nginx:1.30-alpine
COPY --from=build /app/dist /opt/new_dist
COPY nginx.conf /etc/nginx/conf.d/default.conf
RUN echo -e '#!/bin/sh\nrm -rf /usr/share/nginx/html/*\ncp -R /opt/new_dist/* /usr/share/nginx/html/\nexec "$@"' > /entrypoint.sh && chmod +x /entrypoint.sh
EXPOSE 7777
ENTRYPOINT ["/entrypoint.sh"]
CMD ["nginx", "-g", "daemon off;"]


